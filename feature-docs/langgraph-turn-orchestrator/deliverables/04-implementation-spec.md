# Implementation Spec: LangGraph Turn-Orchestrator Replacement

## 1) Mission

Build an experimental LangGraph-backed replacement for the decision-making/control-flow portion of the current embedded turn runner, while preserving the existing TypeScript entry point `runEmbeddedPiAgent(params): Promise<EmbeddedPiRunResult>`. The new path must remain host-owned by the gateway process, reuse current execution and approval surfaces, and fail loudly on sidecar or resume problems. This version is for low-risk experimental rollout, not full parity with every current embedded tool path.

## 1A) Priority and Acceptance Order

The implementation priority order is:

1. Pass `feature-docs/langgraph-turn-orchestrator/deliverables/00a-success-criteria.md`
2. Satisfy `feature-docs/langgraph-turn-orchestrator/deliverables/03-failure-register.md`
3. Preserve the required interfaces and mapping guarantees in this document
4. Satisfy the remaining must-have requirements

Do not treat unit coverage alone as success. The implementation is only complete when the end-to-end success criteria pass through a real Gateway + browser UI run.

## 2) System Responsibilities

- **LangGraph sidecar (Python)** owns:
  - turn-state progression
  - unknown/intent diagnosis
  - execution-request creation
  - verification and final reply decision
- **TS host adapter** owns:
  - preserving the `runEmbeddedPiAgent` interface
  - routing between legacy embedded and LangGraph
  - mapping current TS inputs to sidecar RPC
  - running all execution through existing OpenClaw surfaces
  - translating sidecar output back into `EmbeddedPiRunResult`
- **Feature mode resolver** owns:
  - resolving `legacy` vs `langgraph`
  - defaulting to `legacy`
  - rejecting or explicitly defaulting invalid values
- **Sidecar process manager** owns:
  - spawn/health/stop/restart policy
  - child exit/error detection
  - local-only RPC transport

## 3) Component Specifications

### A. Existing turn-runner integration

- **File location:** `src/agents/pi-embedded-runner/run.ts` (existing)
- **Interface:** keep unchanged

```ts
export async function runEmbeddedPiAgent(
  params: RunEmbeddedPiAgentParams,
): Promise<EmbeddedPiRunResult>;
```

- **Behavior rules**
  1. Preserve current session-lane and global-lane queueing.
  2. Resolve orchestration mode before entering the legacy embedded attempt loop.
  3. If mode is `legacy`, keep the current implementation path unchanged.
  4. If mode is `langgraph`, call the new TS LangGraph adapter instead of the current embedded attempt loop.
  5. Do not change the signature, exported symbol name, or returned TypeScript type.
  6. Keep current callers unaware of which orchestration path ran.
- **Error handling**
  - Pre-turn sidecar failure may fall back to legacy only before LangGraph work starts.
  - After LangGraph work starts, no mid-turn fallback to legacy is allowed.

### B. Orchestration-mode resolver

- **File location:** `src/agents/pi-embedded-runner/langgraph-mode.ts` (new)
- **Config extension points**
  - `src/config/types.agent-defaults.ts` - add a default orchestration-mode field
  - `src/config/types.agents.ts` - add a per-agent orchestration-mode field
  - `src/config/sessions/types.ts` - add a per-session orchestration-mode override field
  - `src/config/zod-schema.agent-defaults.ts` and `src/config/zod-schema.agent-runtime.ts` - schema validation for the new config fields
- **Interface**

```ts
type TurnOrchestrationMode = "legacy" | "langgraph";

function resolveTurnOrchestrationMode(params: {
  cfg?: OpenClawConfig;
  agentId?: string;
  sessionKey?: string;
  sessionEntry?: SessionEntry;
}): TurnOrchestrationMode;
```

- **Behavior rules**
  1. Resolution order: session override -> per-agent config -> global/default config -> `legacy`.
     - If the caller does not already hold `sessionEntry`, the resolver may load it by `sessionKey`.
  2. Do **not** overload `AgentRuntimeConfig.type` (`embedded` / `acp`) for this decision. LangGraph replaces the embedded turn controller, not the ACP runtime.
  3. `langgraph` only applies to embedded-runtime turns.
  4. Invalid values must fail closed or default by one explicit rule covered by tests.
- **Error handling**
  - If the session/agent value is invalid, do not silently choose a random engine.

### C. Sidecar process manager

- **File location:** `src/agents/pi-embedded-runner/langgraph-sidecar.ts` (new)
- **Current-code anchors**
  - `src/process/supervisor/adapters/child.ts`
  - `src/process/child-process-bridge.ts`
- **Interface**

```ts
type GraphRpcClient = {
  ensureStarted(): Promise<void>;
  health(timeoutMs?: number): Promise<{ ok: boolean }>;
  invokeTurn(request: InvokeTurnRequest, timeoutMs: number): Promise<InvokeTurnResponse>;
  resumeTurn(request: ResumeTurnRequest, timeoutMs: number): Promise<ResumeTurnResponse>;
  stop(): Promise<void>;
};
```

- **Behavior rules**
  1. Own one local sidecar child process.
  2. Use local stdio JSON messaging only.
  3. Spawn lazily on first LangGraph turn, or eagerly at gateway start if startup cost proves harmful.
  4. Record health state and child PID.
  5. On child `exit` or `error`, mark the sidecar unhealthy immediately.
  6. Allow one automatic restart before a **new** turn only.
- **Error handling**
  - `health`, `invokeTurn`, and `resumeTurn` must all fail fast on child death or timeout.
  - `stop()` must use graceful stop then force kill.

### D. TS LangGraph adapter

- **File location:** `src/agents/pi-embedded-runner/langgraph-adapter.ts` (new)
- **Interface**

```ts
async function runLangGraphTurn(
  params: RunEmbeddedPiAgentParams,
  deps: {
    sidecar: GraphRpcClient;
  },
): Promise<EmbeddedPiRunResult>;
```

- **Behavior rules**
  1. Build a serializable `GraphTurnRequest` from `RunEmbeddedPiAgentParams`.
  2. Keep host-only values (`AbortSignal`, callbacks, queue handle, mutable refs) outside the RPC request.
  3. Call `invoke_turn`.
  4. If the sidecar interrupts for host execution:
     - run the request through existing host execution/approval surfaces
     - build `GraphExecutionResult`
     - call `resume_turn`
  5. If approval is required, model approval as a host execution result + resume, not as the current async follow-up-only completion path.
  6. Convert the final graph response back into `EmbeddedPiRunResult`.
  7. Enforce the one-retry limit in adapter-visible state as a backstop.
- **Error handling**
  - Validate every sidecar response shape before using it.
  - Unknown execution intents fail closed.
  - Missing required output fields become explicit failures, not partial success.

### E. Python LangGraph sidecar

- **File location:** `assets/langgraph-turn-orchestrator-sidecar/`
- **Location rule**
  - Keep the runtime Python files under `assets/` so they ship with the npm package without adding a new dist-copy pipeline.
  - Resolve the sidecar entry path from the OpenClaw package root at runtime.
- **Required interface**
  - `invoke_turn`
  - `resume_turn`
  - `health`
- **Behavior rules**
  1. Maintain graph checkpoint/resume state for the active turn.
  2. Return only JSON-serializable payloads.
  3. Use the exact state-machine routing in this spec.
  4. Never claim execution success without a host-provided execution result.

## 4) State Machine Definition

### 4.1 State schema

```py
from typing import Any, Literal, NotRequired, TypedDict

class GraphTurnRequest(TypedDict, total=False):
    sessionId: str
    sessionKey: NotRequired[str | None]
    agentId: NotRequired[str | None]
    messageChannel: NotRequired[str | None]
    messageProvider: NotRequired[str | None]
    agentAccountId: NotRequired[str | None]
    trigger: NotRequired[str | None]
    memoryFlushWritePath: NotRequired[str | None]
    messageTo: NotRequired[str | None]
    messageThreadId: NotRequired[str | int | None]
    groupId: NotRequired[str | None]
    groupChannel: NotRequired[str | None]
    groupSpace: NotRequired[str | None]
    spawnedBy: NotRequired[str | None]
    senderId: NotRequired[str | None]
    senderName: NotRequired[str | None]
    senderUsername: NotRequired[str | None]
    senderE164: NotRequired[str | None]
    senderIsOwner: NotRequired[bool]
    currentChannelId: NotRequired[str | None]
    currentThreadTs: NotRequired[str | None]
    currentMessageId: NotRequired[str | int | None]
    replyToMode: NotRequired[Literal["off", "first", "all"] | None]
    requireExplicitMessageTarget: NotRequired[bool]
    disableMessageTool: NotRequired[bool]
    allowGatewaySubagentBinding: NotRequired[bool]
    sessionFile: str
    workspaceDir: str
    agentDir: NotRequired[str | None]
    config: NotRequired[dict[str, Any] | None]
    skillsSnapshot: NotRequired[dict[str, Any] | None]
    prompt: str
    images: NotRequired[list[dict[str, Any]] | None]
    clientTools: NotRequired[list[dict[str, Any]] | None]
    disableTools: NotRequired[bool]
    provider: NotRequired[str | None]
    model: NotRequired[str | None]
    authProfileId: NotRequired[str | None]
    authProfileIdSource: NotRequired[Literal["auto", "user"] | None]
    thinkLevel: NotRequired[str | None]
    fastMode: NotRequired[bool]
    verboseLevel: NotRequired[str | None]
    reasoningLevel: NotRequired[str | None]
    toolResultFormat: NotRequired[str | None]
    suppressToolErrorWarnings: NotRequired[bool]
    bootstrapContextMode: NotRequired[Literal["full", "lightweight"] | None]
    bootstrapContextRunKind: NotRequired[Literal["default", "heartbeat", "cron"] | None]
    bootstrapPromptWarningSignaturesSeen: NotRequired[list[str] | None]
    bootstrapPromptWarningSignature: NotRequired[str | None]
    execOverrides: NotRequired[dict[str, Any] | None]
    bashElevated: NotRequired[dict[str, Any] | None]
    timeoutMs: int
    runId: str
    blockReplyBreak: NotRequired[Literal["text_end", "message_end"] | None]
    blockReplyChunking: NotRequired[dict[str, Any] | None]
    extraSystemPrompt: NotRequired[str | None]
    inputProvenance: NotRequired[dict[str, Any] | None]
    streamParams: NotRequired[dict[str, Any] | None]
    ownerNumbers: NotRequired[list[str] | None]
    enforceFinalTag: NotRequired[bool]
    allowTransientCooldownProbe: NotRequired[bool]

class GraphExecutionRequest(TypedDict, total=False):
    idempotencyKey: str
    intent: Literal["reply", "shell", "approval_request"]
    command: NotRequired[str]
    cwd: NotRequired[str | None]
    requiresApproval: bool
    verificationContract: dict[str, Any]

class GraphExecutionResult(TypedDict, total=False):
    status: Literal["completed", "failed", "approval_pending", "cancelled"]
    ran: bool
    payload: dict[str, Any]
    barrier: NotRequired[dict[str, Any] | None]
    verificationEvidence: NotRequired[dict[str, Any] | None]

class GraphStructuredError(TypedDict):
    kind: str
    message: str

class GraphTurnResponse(TypedDict, total=False):
    payloads: list[dict[str, Any]]
    terminalState: Literal["done", "blocked_waiting_for_user", "escalated", "failed"]
    agentMeta: NotRequired[dict[str, Any] | None]
    stopReason: NotRequired[str | None]
    error: NotRequired[GraphStructuredError | None]
    pendingApprovalDescriptor: NotRequired[dict[str, Any] | None]

class GraphTurnState(TypedDict, total=False):
    turn: GraphTurnRequest
    retryCount: int
    unknowns: list[str]
    blockingReason: NotRequired[str | None]
    intent: NotRequired[Literal["respond", "execute", "ask_clarification", "escalate"] | None]
    executionRequest: NotRequired[GraphExecutionRequest | None]
    executionResult: NotRequired[GraphExecutionResult | None]
    response: NotRequired[GraphTurnResponse | None]
    terminalState: NotRequired[Literal["done", "blocked_waiting_for_user", "escalated", "failed"] | None]
    error: NotRequired[GraphStructuredError | None]
```

### 4.2 Nodes

| Node                      | Inputs                                  | Outputs                                                             |
| ------------------------- | --------------------------------------- | ------------------------------------------------------------------- |
| `ingest_turn`             | `GraphTurnRequest`                      | `GraphTurnState.turn`, `retryCount=0`                               |
| `reconstruct_state`       | current `turn`                          | session/task understanding used by later nodes                      |
| `diagnose_unknowns`       | reconstructed state                     | `unknowns`, optional `blockingReason`                               |
| `decide_intent`           | `turn`, `unknowns`, reconstructed state | `intent` = `respond` / `execute` / `ask_clarification` / `escalate` |
| `build_execution_request` | `intent=execute`, `turn`                | `executionRequest`                                                  |
| `await_host_execution`    | `executionRequest`                      | graph interrupt plus checkpoint id                                  |
| `verify_result`           | `executionResult`, `retryCount`         | final terminal state or one retry                                   |
| `render_reply`            | intent/result/failure state             | `response.payloads`, `response.error`, `response.terminalState`     |
| `persist_turn_artifacts`  | final state                             | checkpoint/state persistence needed for resume                      |

### 4.3 Routing rules

```text
ingest_turn
  -> reconstruct_state
  -> diagnose_unknowns
  -> decide_intent

respond
  -> render_reply
  -> persist_turn_artifacts
  -> done

ask_clarification
  -> render_reply
  -> persist_turn_artifacts
  -> blocked_waiting_for_user

escalate
  -> render_reply
  -> persist_turn_artifacts
  -> escalated

execute
  -> build_execution_request
  -> await_host_execution
  -> resume_turn with GraphExecutionResult
  -> verify_result
      -> render_reply -> persist_turn_artifacts -> done/blocked_waiting_for_user/failed
      -> diagnose_unknowns only when retryCount == 0 and verify_result requests retry
```

### 4.4 Retry bound

- `retryCount` starts at `0`
- `verify_result` may increment it once
- any second retry attempt becomes terminal failure

## 5) RPC Contract

### 5.1 Requests

```ts
type InvokeTurnRequest = {
  requestId: string; // reuse runId
  turn: GraphTurnRequest;
};

type ResumeTurnRequest = {
  requestId: string; // reuse runId
  checkpointId: string;
  turn: GraphTurnRequest;
  executionResult: GraphExecutionResult;
};

type HealthRequest = {};
```

### 5.2 Responses

```ts
type GraphInterrupt = {
  status: "interrupted";
  checkpointId: string;
  graphNode: "await_host_execution";
  executionRequest: GraphExecutionRequest;
};

type GraphCompletion = {
  status: "completed";
  response: GraphTurnResponse;
};

type GraphFailure = {
  status: "failed";
  error: GraphStructuredError;
  response?: GraphTurnResponse;
};

type InvokeTurnResponse = GraphInterrupt | GraphCompletion | GraphFailure;
type ResumeTurnResponse = GraphInterrupt | GraphCompletion | GraphFailure;

type HealthResponse = {
  ok: boolean;
};
```

### 5.3 Contract rules

1. Only JSON-serializable values cross RPC.
2. `requestId` must equal current `runId`.
3. `checkpointId` is required for `resume_turn`.
4. Unknown `status` or `intent` values are adapter errors.
5. `pendingApprovalDescriptor` is internal to the sidecar/host contract only; it has no dedicated field in `EmbeddedPiRunResult`.

## 6) Type Mapping Tables

### 6.1 `RunEmbeddedPiAgentParams` -> `GraphTurnRequest`

| Current field                          | Graph field / disposition                   | Notes                                                      |
| -------------------------------------- | ------------------------------------------- | ---------------------------------------------------------- |
| `sessionId`                            | `turn.sessionId`                            | direct                                                     |
| `sessionKey`                           | `turn.sessionKey`                           | direct                                                     |
| `agentId`                              | `turn.agentId`                              | direct                                                     |
| `messageChannel`                       | `turn.messageChannel`                       | direct                                                     |
| `messageProvider`                      | `turn.messageProvider`                      | direct                                                     |
| `agentAccountId`                       | `turn.agentAccountId`                       | direct                                                     |
| `trigger`                              | `turn.trigger`                              | direct                                                     |
| `memoryFlushWritePath`                 | `turn.memoryFlushWritePath`                 | direct                                                     |
| `messageTo`                            | `turn.messageTo`                            | direct                                                     |
| `messageThreadId`                      | `turn.messageThreadId`                      | direct                                                     |
| `groupId`                              | `turn.groupId`                              | direct                                                     |
| `groupChannel`                         | `turn.groupChannel`                         | direct                                                     |
| `groupSpace`                           | `turn.groupSpace`                           | direct                                                     |
| `spawnedBy`                            | `turn.spawnedBy`                            | direct                                                     |
| `senderId`                             | `turn.senderId`                             | direct                                                     |
| `senderName`                           | `turn.senderName`                           | direct                                                     |
| `senderUsername`                       | `turn.senderUsername`                       | direct                                                     |
| `senderE164`                           | `turn.senderE164`                           | direct                                                     |
| `senderIsOwner`                        | `turn.senderIsOwner`                        | direct                                                     |
| `currentChannelId`                     | `turn.currentChannelId`                     | direct                                                     |
| `currentThreadTs`                      | `turn.currentThreadTs`                      | direct                                                     |
| `currentMessageId`                     | `turn.currentMessageId`                     | direct                                                     |
| `replyToMode`                          | `turn.replyToMode`                          | direct                                                     |
| `hasRepliedRef`                        | host-only                                   | mutable ref; not serializable                              |
| `requireExplicitMessageTarget`         | `turn.requireExplicitMessageTarget`         | direct                                                     |
| `disableMessageTool`                   | `turn.disableMessageTool`                   | direct                                                     |
| `allowGatewaySubagentBinding`          | `turn.allowGatewaySubagentBinding`          | direct                                                     |
| `sessionFile`                          | `turn.sessionFile`                          | direct                                                     |
| `workspaceDir`                         | `turn.workspaceDir`                         | direct                                                     |
| `agentDir`                             | `turn.agentDir`                             | direct                                                     |
| `config`                               | `turn.config`                               | serialized `OpenClawConfig` snapshot                       |
| `skillsSnapshot`                       | `turn.skillsSnapshot`                       | serialized snapshot                                        |
| `prompt`                               | `turn.prompt`                               | direct                                                     |
| `images`                               | `turn.images`                               | serialized `ImageContent[]`                                |
| `clientTools`                          | `turn.clientTools`                          | direct in contract, but **Gap** for v1 shell-only behavior |
| `disableTools`                         | `turn.disableTools`                         | direct                                                     |
| `provider`                             | `turn.provider`                             | direct                                                     |
| `model`                                | `turn.model`                                | direct                                                     |
| `authProfileId`                        | `turn.authProfileId`                        | direct                                                     |
| `authProfileIdSource`                  | `turn.authProfileIdSource`                  | direct                                                     |
| `thinkLevel`                           | `turn.thinkLevel`                           | direct                                                     |
| `fastMode`                             | `turn.fastMode`                             | direct                                                     |
| `verboseLevel`                         | `turn.verboseLevel`                         | direct                                                     |
| `reasoningLevel`                       | `turn.reasoningLevel`                       | direct                                                     |
| `toolResultFormat`                     | `turn.toolResultFormat`                     | direct                                                     |
| `suppressToolErrorWarnings`            | `turn.suppressToolErrorWarnings`            | direct                                                     |
| `bootstrapContextMode`                 | `turn.bootstrapContextMode`                 | direct                                                     |
| `bootstrapContextRunKind`              | `turn.bootstrapContextRunKind`              | direct                                                     |
| `bootstrapPromptWarningSignaturesSeen` | `turn.bootstrapPromptWarningSignaturesSeen` | direct                                                     |
| `bootstrapPromptWarningSignature`      | `turn.bootstrapPromptWarningSignature`      | direct                                                     |
| `execOverrides`                        | `turn.execOverrides`                        | direct                                                     |
| `bashElevated`                         | `turn.bashElevated`                         | direct                                                     |
| `timeoutMs`                            | `turn.timeoutMs`                            | direct                                                     |
| `runId`                                | `turn.runId` and RPC `requestId`            | direct                                                     |
| `abortSignal`                          | host-only                                   | adapter-owned cancellation only                            |
| `shouldEmitToolResult`                 | host-only                                   | callback/presentation control                              |
| `shouldEmitToolOutput`                 | host-only                                   | callback/presentation control                              |
| `onPartialReply`                       | host-only                                   | callback                                                   |
| `onAssistantMessageStart`              | host-only                                   | callback                                                   |
| `onBlockReply`                         | host-only                                   | callback                                                   |
| `onBlockReplyFlush`                    | host-only                                   | callback                                                   |
| `blockReplyBreak`                      | `turn.blockReplyBreak`                      | direct                                                     |
| `blockReplyChunking`                   | `turn.blockReplyChunking`                   | direct                                                     |
| `onReasoningStream`                    | host-only                                   | callback                                                   |
| `onReasoningEnd`                       | host-only                                   | callback                                                   |
| `onToolResult`                         | host-only                                   | callback                                                   |
| `onAgentEvent`                         | host-only                                   | callback                                                   |
| `lane`                                 | host-only                                   | scheduling only                                            |
| `enqueue`                              | host-only                                   | function, not serializable                                 |
| `extraSystemPrompt`                    | `turn.extraSystemPrompt`                    | direct                                                     |
| `inputProvenance`                      | `turn.inputProvenance`                      | serialized                                                 |
| `streamParams`                         | `turn.streamParams`                         | serialized                                                 |
| `ownerNumbers`                         | `turn.ownerNumbers`                         | direct                                                     |
| `enforceFinalTag`                      | `turn.enforceFinalTag`                      | direct                                                     |
| `allowTransientCooldownProbe`          | `turn.allowTransientCooldownProbe`          | direct                                                     |

### 6.2 `GraphTurnResponse` + host execution state -> `EmbeddedPiRunResult`

| Output field                                  | Source                                                      | Notes                                                                                                          |
| --------------------------------------------- | ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `payloads`                                    | `GraphTurnResponse.payloads`                                | direct                                                                                                         |
| `meta.durationMs`                             | host adapter wall-clock measurement                         | current runner already measures duration in TS                                                                 |
| `meta.agentMeta.sessionId`                    | `GraphTurnResponse.agentMeta.sessionId` or `turn.sessionId` | must be explicit                                                                                               |
| `meta.agentMeta.provider`                     | `GraphTurnResponse.agentMeta.provider`                      | direct if graph supplies; otherwise `Gap`                                                                      |
| `meta.agentMeta.model`                        | `GraphTurnResponse.agentMeta.model`                         | direct if graph supplies; otherwise `Gap`                                                                      |
| `meta.agentMeta.compactionCount`              | `GraphTurnResponse.agentMeta.compactionCount`               | `Gap` unless LangGraph path reproduces current compaction accounting                                           |
| `meta.agentMeta.promptTokens`                 | `GraphTurnResponse.agentMeta.promptTokens`                  | `Gap` unless graph reports equivalent usage                                                                    |
| `meta.agentMeta.usage`                        | `GraphTurnResponse.agentMeta.usage`                         | direct if graph reports it                                                                                     |
| `meta.agentMeta.lastCallUsage`                | `GraphTurnResponse.agentMeta.lastCallUsage`                 | `Gap` unless graph reports it                                                                                  |
| `meta.aborted`                                | host adapter abort tracking                                 | host-owned                                                                                                     |
| `meta.systemPromptReport`                     | host-built report or `Gap`                                  | current report is built inside existing embedded attempt path                                                  |
| `meta.error`                                  | `GraphTurnResponse.error` or host-side RPC failure mapping  | must stay structured                                                                                           |
| `meta.stopReason`                             | `GraphTurnResponse.stopReason`                              | direct                                                                                                         |
| `meta.pendingToolCalls`                       | `Gap`                                                       | current field is tied to existing client-tool-call path; v1 shell-only LangGraph path has no proven equivalent |
| `didSendViaMessagingTool`                     | host execution side-effect tracking                         | `Gap` for v1 shell-only unless host execution path explicitly records it                                       |
| `messagingToolSentTexts`                      | host execution side-effect tracking                         | `Gap` for v1 shell-only                                                                                        |
| `messagingToolSentMediaUrls`                  | host execution side-effect tracking                         | `Gap` for v1 shell-only                                                                                        |
| `messagingToolSentTargets`                    | host execution side-effect tracking                         | `Gap` for v1 shell-only                                                                                        |
| `successfulCronAdds`                          | host execution side-effect tracking                         | `Gap` for v1 shell-only                                                                                        |
| `GraphTurnResponse.pendingApprovalDescriptor` | `Gap`                                                       | no dedicated `EmbeddedPiRunResult` field exists; external caller visibility must stay payload-based            |

## 7) Failure-Mode Matrix

| FM ID    | Spec section(s) |
| -------- | --------------- |
| FM-L1-01 | 3B, 3C, 5       |
| FM-L1-02 | 3A, 3C, 3D, 5   |
| FM-L1-03 | 3C, 3D, 5       |
| FM-L1-04 | 4               |
| FM-L1-05 | 4, 5            |
| FM-L1-06 | 4               |
| FM-L2-01 | 6.1             |
| FM-L2-02 | 6.2             |
| FM-L2-03 | 3C, 3D, 5       |
| FM-L2-04 | 3C              |
| FM-L2-05 | 5, 6.1          |
| FM-L2-06 | 3B              |
| FM-L2-07 | 3D, 5           |
| FM-L2-08 | 3A, 3D          |

## 8) Test Matrix

### End-to-end acceptance tests

- `browser_ui_local_gateway_can_start_unconfigured_dev_mode`
- `browser_ui_prompt_runs_through_langgraph_not_legacy`
- `langgraph_sidecar_starts_during_real_gateway_turn`
- `real_browser_ui_turn_returns_final_reply`
- `forced_failure_is_loud_and_gateway_stays_healthy`

### Parity and routing tests

- `preserves_run_embedded_pi_agent_signature_and_return_type`
- `default_mode_routes_to_legacy_embedded`
- `agent_or_session_langgraph_mode_routes_to_langgraph`
- `same_session_langgraph_turns_are_serialized`

### Graph behavior tests

- `respond_path_returns_payloads_without_execution`
- `clarification_path_returns_blocked_reply_without_execution`
- `execute_path_interrupts_for_shell_request`
- `verify_result_allows_only_one_retry`

### Failure tests

- `falls_back_to_legacy_when_sidecar_health_fails_before_new_turn`
- `fails_loudly_when_sidecar_exits_mid_turn`
- `invoke_turn_timeout_returns_structured_failure`
- `unknown_execution_intent_fails_closed`
- `invalid_orchestration_mode_is_rejected_or_defaulted_explicitly`

### Mapping tests

- `graph_turn_request_mapping_is_field_complete`
- `graph_turn_response_mapping_is_field_complete`
- `invoke_turn_request_rejects_non_serializable_fields`

### Approval/resume tests

- `approval_resume_round_trips_checkpoint_state`
- `restart_without_checkpoint_fails_explicitly_on_resume`

## 9) Non-Goals (Explicit)

- No L3 wire-level hardening
- No horizontal scaling
- No production rollout procedures
- No deterministic action templates beyond the v1 shell path
- No multi-turn graph memory beyond turn-local checkpoint/resume state
- `memory_write`, `email_send`, and `http_call` intents are not implemented
- Full parity with the current broad embedded tool surface is not a v1 goal
  - Current code supports many tools through `createOpenClawCodingTools`
  - V1 LangGraph execute intent is shell-only by design

## 10) Discovery Questions for Implementing Agent

1. **Python package location**
   - Resolved: use `assets/langgraph-turn-orchestrator-sidecar/`.
   - Reason: `assets/` already ships in the package, so the sidecar can be found from package root in both local repo runs and installed-package runs without inventing a second runtime-asset pipeline.
2. **System prompt report parity**
   - Current `EmbeddedPiRunResult.meta.systemPromptReport` comes from the existing embedded attempt path.
   - I found the field in `src/agents/pi-embedded-runner/types.ts` but did not find a clean existing adapter seam that would preserve it automatically for LangGraph.
3. **Approval descriptor mapping**
   - The design intent includes a conceptual pending-approval descriptor in the turn response.
   - `EmbeddedPiRunResult` has no dedicated field for that; only payload text and generic `meta` fields exist.
4. **Client tool call parity**
   - Current `EmbeddedPiRunResult.meta.pendingToolCalls` exists in the embedded runner.
   - V1 shell-only LangGraph design has no proven equivalent path for that field.
5. **Messaging-tool side-effect fields**
   - Current result fields `didSendViaMessagingTool`, `messagingToolSentTexts`, `messagingToolSentMediaUrls`, `messagingToolSentTargets`, and `successfulCronAdds` are host-side tool side effects.
   - If v1 LangGraph stays shell-only, decide whether LangGraph-eligible rollout must exclude turns that depend on those fields.
6. **Session override storage**
   - `SessionEntry` is the obvious existing place for a per-session orchestration override, but no such field exists today.
   - Verify whether the override should live in `SessionEntry` or another session-scoped store.

## 11) Rollout Sequence

1. Add orchestration-mode config + session override plumbing; default remains `legacy`.
2. Add sidecar process manager and TS adapter, but do not route live turns yet.
3. Implement `respond`, `ask_clarification`, and `escalate`.
4. Implement `execute` with shell-only host execution and approval resume.
5. Enable LangGraph for one low-risk embedded agent/session cohort.
6. Expand only after the field-mapping and failure-mode tests stay green.
7. Consider broader tool-surface parity later; do not widen the rollout before that gap is addressed.

## 12) Traceability

### Requirements -> Spec sections

| Requirement | Spec section(s) |
| ----------- | --------------- |
| FR-001      | 3A              |
| FR-002      | 3B, 11          |
| FR-003      | 4, 5            |
| FR-004      | 4, 5            |
| FR-005      | 3D, 5           |
| FR-006      | 3D, 5           |
| FR-007      | 4               |
| IR-001      | 6.1             |
| IR-002      | 6.2             |
| IR-003      | 3D, 5, 6.1      |
| FHR-001     | 3B              |
| FHR-002     | 3C, 5, 11       |
| FHR-003     | 3A, 3C, 3D, 5   |
| FHR-004     | 3C, 5           |
| FHR-005     | 3C, 3D, 5, 10   |
| NFR-001     | 3A              |
| NFR-002     | 3C, 3D, 5       |
| NFR-003     | 3B, 11          |

### Failure modes -> Spec sections + tests

| Failure mode | Spec sections | Test case                                                         |
| ------------ | ------------- | ----------------------------------------------------------------- |
| FM-L1-01     | 3B, 3C, 5     | `falls_back_to_legacy_when_sidecar_health_fails_before_new_turn`  |
| FM-L1-02     | 3A, 3C, 3D, 5 | `fails_loudly_when_sidecar_exits_mid_turn`                        |
| FM-L1-03     | 3C, 3D, 5     | `invoke_turn_timeout_returns_structured_failure`                  |
| FM-L1-04     | 4             | `intent_router_selects_clarification_without_execution`           |
| FM-L1-05     | 4, 5          | `verify_result_accepts_completed_execution_with_success_evidence` |
| FM-L1-06     | 4             | `verify_result_allows_only_one_retry`                             |
| FM-L2-01     | 6.1           | `graph_turn_request_mapping_is_field_complete`                    |
| FM-L2-02     | 6.2           | `graph_turn_response_mapping_is_field_complete`                   |
| FM-L2-03     | 3C, 3D, 5     | `approval_resume_round_trips_checkpoint_state`                    |
| FM-L2-04     | 3C            | `host_marks_sidecar_unhealthy_on_nonzero_exit`                    |
| FM-L2-05     | 5, 6.1        | `invoke_turn_request_rejects_non_serializable_fields`             |
| FM-L2-06     | 3B            | `invalid_orchestration_mode_is_rejected_or_defaulted_explicitly`  |
| FM-L2-07     | 3D, 5         | `unknown_execution_intent_fails_closed`                           |
| FM-L2-08     | 3A, 3D        | `same_session_langgraph_turns_are_serialized`                     |

## 13) Evidence

- `src/agents/pi-embedded-runner/run.ts` - `runEmbeddedPiAgent`, current public boundary and queueing
- `src/agents/pi-embedded-runner/run/params.ts` - `RunEmbeddedPiAgentParams`
- `src/agents/pi-embedded-runner/types.ts` - `EmbeddedPiRunResult`, `EmbeddedPiRunMeta`, `EmbeddedPiAgentMeta`
- `src/agents/pi-embedded-runner/run/attempt.ts` - current embedded execution breadth through `createAgentSession`, `subscribeEmbeddedPiSession`, `createOpenClawCodingTools`
- `src/process/command-queue.ts` - session-lane serialization
- `src/agents/pi-embedded-runner/lanes.ts` - lane derivation
- `src/config/types.agents.ts` - `AgentRuntimeConfig`, current `embedded` / `acp` split
- `src/config/types.agent-defaults.ts` - `AgentDefaultsConfig`, current default-agent config surface
- `src/config/sessions/types.ts` - `SessionEntry`, current session persistence surface
- `src/config/zod-schema.agent-defaults.ts` - `AgentDefaultsSchema`, config-schema extension point
- `src/config/zod-schema.agent-runtime.ts` - `AgentEntrySchema`, per-agent schema extension point
- `src/process/supervisor/adapters/child.ts` - `createChildAdapter`, child-process pattern
- `src/process/child-process-bridge.ts` - `attachChildProcessBridge`, signal-forwarding pattern
- `src/cli/gateway-cli/run-loop.ts` - `runGatewayLoop`, lifecycle owner
- `src/agents/bash-tools.exec.ts` - host execution routing
- `src/agents/bash-tools.exec-host-gateway.ts` - gateway approval flow, async follow-up pattern
- `src/agents/bash-tools.exec-host-node.ts` - node approval flow, async follow-up pattern
- `src/agents/bash-tools.exec-approval-request.ts` - approval RPC calls through gateway tools
- `src/agents/bash-tools.exec-host-shared.ts` - approval request/result helper shapes
- `src/agents/pi-embedded-subscribe.handlers.tools.ts` - deterministic approval prompt emission
- `src/gateway/server-methods/exec-approval.ts` - approval request/wait/resolve methods
- `src/gateway/exec-approval-manager.ts` - in-memory approval state
- `feature-docs/langgraph-turn-orchestrator/spec/01-design-intent.md` - target graph structure, retry bound, sidecar RPC commands, rollout intent
