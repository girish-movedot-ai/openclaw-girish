# System Profile: LangGraph Turn-Orchestrator Replacement

## 1) Feature Classification

- Feature type: Internal infrastructure refactor of the embedded turn runner.
- Runtime mode: Async Node.js turn execution. `runEmbeddedPiAgent` returns a `Promise<EmbeddedPiRunResult>` and serializes work through session and global command lanes before entering the run loop.
- Production exposure: High. The current embedded runner is used by gateway-triggered user turns, auto-reply turns, cron isolated turns, memory flush turns, voice-call responses, auth probes, and plugin/runtime helpers.
- Data sensitivity: High. `RunEmbeddedPiAgentParams` carries end-user prompt text, images, sender identity fields (`senderId`, `senderName`, `senderUsername`, `senderE164`), session/workspace identifiers, auth profile selection, and callbacks that drive outward delivery.
- Blast radius if wrong: High. Breaking this path can break agent turns across gateway, auto-reply, cron, plugins, and voice-call flows.

## 2) Dependency Topology

### Internal Dependencies

- Command serialization: `src/agents/pi-embedded-runner/run.ts` - `runEmbeddedPiAgent`, `src/agents/pi-embedded-runner/lanes.ts` - `resolveSessionLane`, `resolveGlobalLane`, `src/process/command-queue.ts` - `enqueueCommandInLane`
- Workspace resolution: `src/agents/workspace-run.ts` - `resolveRunWorkspaceDir`
- Model and auth resolution: `src/agents/pi-embedded-runner/run.ts` - `resolveModelAsync`, `getApiKeyForModel`, `prepareProviderRuntimeAuth`, `resolveAuthProfileOrder`
- Context engine and compaction: `src/agents/pi-embedded-runner/run.ts` - `resolveContextEngine`, `evaluateContextWindowGuard`; `src/agents/pi-embedded-runner/run/attempt.ts` - `prepareSessionManagerForRun`, compaction-related helpers
- Embedded session execution: `src/agents/pi-embedded-runner/run/attempt.ts` - `createAgentSession`, `SessionManager.open`, `subscribeEmbeddedPiSession`, `createOpenClawCodingTools`
- Active run state: `src/agents/pi-embedded-runner/runs.ts` - `setActiveEmbeddedRun`, `clearActiveEmbeddedRun`, `waitForActiveEmbeddedRuns`
- Approval UX and follow-up execution: `src/agents/bash-tools.exec.ts`, `src/agents/bash-tools.exec-host-gateway.ts`, `src/agents/bash-tools.exec-host-node.ts`, `src/agents/pi-embedded-subscribe.handlers.tools.ts`, `src/gateway/server-methods/exec-approval.ts`, `src/gateway/exec-approval-manager.ts`
- Gateway ingress and delivery: `src/gateway/server-methods/agent.ts` - `agentHandlers.agent`, `dispatchAgentRunFromGateway`; `src/agents/agent-command.ts` - `agentCommandFromIngress`, `runAgentAttempt`
- Session persistence: `src/config/sessions/types.ts` - `SessionEntry`, `src/config/sessions.js` consumers across gateway, auto-reply, and cron paths

### External Dependencies

- LLM/provider SDKs through the embedded runner stack:
  - `package.json` - dependencies on `@mariozechner/pi-ai`, `@mariozechner/pi-agent-core`, `@mariozechner/pi-coding-agent`
  - `src/agents/pi-embedded-runner/run/attempt.ts` - `streamSimple`, `createAgentSession`
- Local process execution:
  - `src/process/supervisor/adapters/child.ts` - `createChildAdapter`
  - `src/process/child-process-bridge.ts` - `attachChildProcessBridge`
- Approval transport over gateway methods:
  - `src/agents/bash-tools.exec-approval-request.ts` - `callGatewayTool("exec.approval.request")`, `callGatewayTool("exec.approval.waitDecision")`

## 3) Trust Boundaries

1. Gateway client/request boundary
   - `src/gateway/server-methods/agent.ts` - `agentHandlers.agent` validates inbound gateway request params before it constructs an agent turn.
2. Embedded runner to model/provider boundary
   - `src/agents/pi-embedded-runner/run.ts` - `resolveModelAsync`, auth resolution, provider/runtime auth setup
   - `src/agents/pi-embedded-runner/run/attempt.ts` - `streamSimple`, `createAgentSession`
3. Embedded runner to filesystem/session transcript boundary
   - `src/agents/pi-embedded-runner/run.ts` - `resolveRunWorkspaceDir`, `fs.mkdir`
   - `src/agents/pi-embedded-runner/run/attempt.ts` - `SessionManager.open`, `prepareSessionManagerForRun`
4. Embedded runner to tool execution boundary
   - `src/agents/pi-embedded-runner/run/attempt.ts` - `createOpenClawCodingTools`
   - `src/agents/bash-tools.exec.ts` - host/sandbox/node exec routing
5. Embedded runner to approval boundary
   - `src/agents/bash-tools.exec-host-gateway.ts` and `src/agents/bash-tools.exec-host-node.ts` register approvals with the gateway and return `approval-pending` tool results
   - `src/gateway/server-methods/exec-approval.ts` and `src/gateway/exec-approval-manager.ts` hold approval state inside the gateway process

## 4) State and Concurrency Surface

- Session/global queue serialization
  - `src/agents/pi-embedded-runner/run.ts` resolves a per-session lane from `params.sessionKey ?? params.sessionId` and a global lane from `params.lane`, then nests both `enqueueCommandInLane` calls.
- Active in-memory run registry
  - `src/agents/pi-embedded-runner/runs.ts` stores active run handles and snapshots in global singleton maps keyed by `sessionId`.
- Session metadata persistence
  - `src/config/sessions/types.ts` - `SessionEntry` stores session IDs, runtime model fields, auth overrides, delivery context, compaction counts, `systemPromptReport`, and other run state.
- Approval pending state
  - `src/gateway/exec-approval-manager.ts` - `ExecApprovalManager` stores approvals in an in-memory `Map<string, PendingEntry>`.
  - Mismatch with design intent: approval state is not persisted across gateway restart in current code.
- Current approval continuation model
  - `src/agents/bash-tools.exec-host-gateway.ts` and `src/agents/bash-tools.exec-host-node.ts` return a pending tool result immediately, then run an async follow-up after approval resolution. The original turn does not resume through `runEmbeddedPiAgent`.
- Existing runtime routing
  - `src/config/types.agents.ts` - `AgentRuntimeConfig` already switches agents between `embedded` and `acp`.
  - Gap: no `langgraph` mode exists, and `src/config/sessions/types.ts` has no session-level orchestration mode field.

## 5) Key Interfaces (Extracted from Code)

### Turn Runner Entry Point

```ts
export async function runEmbeddedPiAgent(
  params: RunEmbeddedPiAgentParams,
): Promise<EmbeddedPiRunResult>
```

### Turn Runner Input Type

```ts
export type ClientToolDefinition = {
  type: "function";
  function: {
    name: string;
    description?: string;
    parameters?: Record<string, unknown>;
  };
};

export type RunEmbeddedPiAgentParams = {
  sessionId: string;
  sessionKey?: string;
  agentId?: string;
  messageChannel?: string;
  messageProvider?: string;
  agentAccountId?: string;
  trigger?: string;
  memoryFlushWritePath?: string;
  messageTo?: string;
  messageThreadId?: string | number;
  groupId?: string | null;
  groupChannel?: string | null;
  groupSpace?: string | null;
  spawnedBy?: string | null;
  senderId?: string | null;
  senderName?: string | null;
  senderUsername?: string | null;
  senderE164?: string | null;
  senderIsOwner?: boolean;
  currentChannelId?: string;
  currentThreadTs?: string;
  currentMessageId?: string | number;
  replyToMode?: "off" | "first" | "all";
  hasRepliedRef?: { value: boolean };
  requireExplicitMessageTarget?: boolean;
  disableMessageTool?: boolean;
  allowGatewaySubagentBinding?: boolean;
  sessionFile: string;
  workspaceDir: string;
  agentDir?: string;
  config?: OpenClawConfig;
  skillsSnapshot?: SkillSnapshot;
  prompt: string;
  images?: ImageContent[];
  clientTools?: ClientToolDefinition[];
  disableTools?: boolean;
  provider?: string;
  model?: string;
  authProfileId?: string;
  authProfileIdSource?: "auto" | "user";
  thinkLevel?: ThinkLevel;
  fastMode?: boolean;
  verboseLevel?: VerboseLevel;
  reasoningLevel?: ReasoningLevel;
  toolResultFormat?: ToolResultFormat;
  suppressToolErrorWarnings?: boolean;
  bootstrapContextMode?: "full" | "lightweight";
  bootstrapContextRunKind?: "default" | "heartbeat" | "cron";
  bootstrapPromptWarningSignaturesSeen?: string[];
  bootstrapPromptWarningSignature?: string;
  execOverrides?: Pick<ExecToolDefaults, "host" | "security" | "ask" | "node">;
  bashElevated?: ExecElevatedDefaults;
  timeoutMs: number;
  runId: string;
  abortSignal?: AbortSignal;
  shouldEmitToolResult?: () => boolean;
  shouldEmitToolOutput?: () => boolean;
  onPartialReply?: (payload: { text?: string; mediaUrls?: string[] }) => void | Promise<void>;
  onAssistantMessageStart?: () => void | Promise<void>;
  onBlockReply?: (payload: BlockReplyPayload) => void | Promise<void>;
  onBlockReplyFlush?: () => void | Promise<void>;
  blockReplyBreak?: "text_end" | "message_end";
  blockReplyChunking?: BlockReplyChunking;
  onReasoningStream?: (payload: { text?: string; mediaUrls?: string[] }) => void | Promise<void>;
  onReasoningEnd?: () => void | Promise<void>;
  onToolResult?: (payload: ReplyPayload) => void | Promise<void>;
  onAgentEvent?: (evt: { stream: string; data: Record<string, unknown> }) => void;
  lane?: string;
  enqueue?: typeof enqueueCommand;
  extraSystemPrompt?: string;
  inputProvenance?: InputProvenance;
  streamParams?: AgentStreamParams;
  ownerNumbers?: string[];
  enforceFinalTag?: boolean;
  allowTransientCooldownProbe?: boolean;
};
```

### Turn Runner Output Type

```ts
export type EmbeddedPiAgentMeta = {
  sessionId: string;
  provider: string;
  model: string;
  compactionCount?: number;
  promptTokens?: number;
  usage?: {
    input?: number;
    output?: number;
    cacheRead?: number;
    cacheWrite?: number;
    total?: number;
  };
  lastCallUsage?: {
    input?: number;
    output?: number;
    cacheRead?: number;
    cacheWrite?: number;
    total?: number;
  };
};

export type EmbeddedPiRunMeta = {
  durationMs: number;
  agentMeta?: EmbeddedPiAgentMeta;
  aborted?: boolean;
  systemPromptReport?: SessionSystemPromptReport;
  error?: {
    kind:
      | "context_overflow"
      | "compaction_failure"
      | "role_ordering"
      | "image_size"
      | "retry_limit";
    message: string;
  };
  stopReason?: string;
  pendingToolCalls?: Array<{
    id: string;
    name: string;
    arguments: string;
  }>;
};

export type EmbeddedPiRunResult = {
  payloads?: Array<{
    text?: string;
    mediaUrl?: string;
    mediaUrls?: string[];
    replyToId?: string;
    isError?: boolean;
  }>;
  meta: EmbeddedPiRunMeta;
  didSendViaMessagingTool?: boolean;
  messagingToolSentTexts?: string[];
  messagingToolSentMediaUrls?: string[];
  messagingToolSentTargets?: MessagingToolSend[];
  successfulCronAdds?: number;
};
```

### Caller Inventory

| Caller module | Real caller symbol | Parameters used | Result handling |
|---|---|---|---|
| `src/agents/agent-command.ts` | `runAgentAttempt` | Builds the main user/gateway turn with session/workspace/model/auth/prompt/timeouts and passes channel context. | Returns the `EmbeddedPiRunResult` back to agent command delivery/gateway handling. |
| `src/auto-reply/reply/agent-runner-execution.ts` | `runAgentTurnWithFallback` | Adds streaming callbacks (`onPartialReply`, `onToolResult`, `onAgentEvent`, `onBlockReply*`), sender metadata, group context, and fallback retry options. | Uses `result.meta.error` for session reset logic, tracks compaction, and returns normalized final reply payloads. |
| `src/auto-reply/reply/followup-runner.ts` | closure returned by `createFollowupRunner` | Passes queued follow-up turn context, sender metadata, delivery targets, and prompt. | Persists usage/system prompt report, filters duplicate messaging-tool payloads, and sends follow-up payloads. |
| `src/auto-reply/reply/agent-runner-memory.ts` | `runMemoryFlushIfNeeded` | Runs trigger=`"memory"` with `memoryFlushWritePath` and compaction event observation. | Updates session memory-flush metadata and compaction counters; ignores normal payload delivery. |
| `src/cron/isolated-agent/run.ts` | `runCronIsolatedAgentTurn` | Runs trigger=`"cron"` with explicit cron delivery context, tool policy, timeout, and bootstrap context mode. | May issue a second turn for interim acknowledgements, then persists telemetry and delivery output. |
| `src/commands/models/list.probe.ts` | `probeTarget` | Uses probe session/workspace, provider/model/auth profile, fixed prompt, and low verbosity. | Ignores payloads and treats success/failure as auth probe status. |
| `src/hooks/llm-slug-generator.ts` | `generateSlugViaLLM` | Creates a one-off temp session and prompt. | Reads the first payload text and normalizes it into a slug. |
| `extensions/voice-call/src/response-generator.ts` | `generateVoiceResponse` | Uses voice-specific `messageProvider`, temp `runId`, timeout, and extra system prompt. | Concatenates non-error payload texts into a voice response. |
| `extensions/llm-task/src/llm-task-tool.ts` | `createLlmTaskTool` tool handler | Runs with `disableTools: true`, JSON-only prompt, temp session file, and optional auth profile. | Collects payload text, parses JSON, and validates against schema. |
| `src/plugins/runtime/runtime-agent.ts` | `createRuntimeAgent` | Exposes `runEmbeddedPiAgent` to plugins through the plugin runtime surface. | No result transformation; direct runtime export. |
| `src/gateway/server-methods/agent.ts` | `agentHandlers.agent` -> `dispatchAgentRunFromGateway` -> `agentCommandFromIngress` | Indirect top-level gateway ingress for user-triggered runs. | Stores dedupe entries and returns the final agent command result to gateway clients. |

### Approval Flow

1. A tool execution path reaches host exec handling.
   - `src/agents/bash-tools.exec.ts` routes host execution through `processGatewayAllowlist(...)` for `host === "gateway"` and through `executeNodeHostCommand(...)` for `host === "node"`.
2. If approval is required, the exec host implementation registers an approval with the gateway.
   - `src/agents/bash-tools.exec-host-gateway.ts` - `createAndRegisterDefaultExecApprovalRequest`
   - `src/agents/bash-tools.exec-host-node.ts` - same pattern for node execution
   - Both use `src/agents/bash-tools.exec-approval-request.ts` - `registerExecApprovalRequestForHostOrThrow`
3. The tool returns a pending tool result immediately.
   - `src/agents/bash-tools.exec-host-gateway.ts` - returns `pendingResult: buildExecApprovalPendingToolResult(...)`
   - `src/agents/bash-tools.exec-host-node.ts` - returns `buildExecApprovalPendingToolResult(...)`
4. The embedded subscribe layer detects the pending approval payload and emits a deterministic approval reply.
   - `src/agents/pi-embedded-subscribe.handlers.tools.ts` - `readExecApprovalPendingDetails(...)`, `buildExecApprovalPendingReplyPayload(...)`, sets `ctx.state.deterministicApprovalPromptSent = true`
5. The main run path suppresses duplicate assistant text after a deterministic approval prompt.
   - `src/agents/pi-embedded-runner/run.ts` forwards `attempt.didSendDeterministicApprovalPrompt` into `buildEmbeddedRunPayloads(...)`.
   - `src/agents/pi-embedded-runner/run/payloads.ts` suppresses assistant artifacts when `didSendDeterministicApprovalPrompt === true`.
6. The gateway owns the pending approval state in memory.
   - `src/gateway/server-methods/exec-approval.ts` registers and resolves approvals.
   - `src/gateway/exec-approval-manager.ts` stores them in `private pending = new Map<string, PendingEntry>()`.
7. After the approval decision arrives, the exec host path runs asynchronously and sends a follow-up message.
   - `src/agents/bash-tools.exec-host-gateway.ts` runs `runExecProcess(...)` after approval and sends `sendExecApprovalFollowupResult(...)`
   - `src/agents/bash-tools.exec-host-node.ts` invokes `node.invoke` after approval and sends `sendExecApprovalFollowupResult(...)`

**Current-code mismatch with design intent:** approval is not a turn-resume path today. The original embedded turn does not call back into `runEmbeddedPiAgent` with an approval payload. The gateway approval state is also not persisted across restart.

## 6) Infrastructure Facts

- Node version: `>=22.16.0` (`package.json`)
- TypeScript version: `^5.9.3` (`package.json`)
- Python version: Unknown. The repo root `pyproject.toml` only sets Ruff `target-version = "py310"`; that is a lint target, not a proven runtime.
- LangGraph version: Gap. Repo search found no LangGraph dependency or Python runtime module for this feature.
- Current runtime dispatch mechanism:
  - Existing: `src/config/types.agents.ts` - `AgentRuntimeConfig` supports `type: "embedded"` or `type: "acp"`
  - Gap: no `langgraph` runtime value and no session-level runtime/orchestration override field were found
- Deployment/process lifecycle:
  - `src/cli/gateway-cli/run-loop.ts` - `runGatewayLoop` owns gateway lifecycle, signal handling, restart drain, and active-run shutdown behavior
  - Gap: no Python sidecar process manager exists in the current codebase
- IPC patterns already present:
  - Gateway method RPC: `src/gateway/server-methods/*.ts`
  - Child-process stdio adapter: `src/process/supervisor/adapters/child.ts` - `createChildAdapter`
  - Parent-to-child signal bridge: `src/process/child-process-bridge.ts` - `attachChildProcessBridge`
  - Approval follow-up RPC through gateway tool calls: `src/agents/bash-tools.exec-approval-request.ts`
- Feature flag system:
  - Mismatch with design intent: the repo is not starting from zero. It already has agent runtime selection (`embedded` vs `acp`).
  - Gap: no `langgraph` option, no `legacy`/`langgraph` value pair, and no per-session override for this feature were found.

## 7) FMEA Depth Target

- Level 1 (workflow/system): Required
- Level 2 (component/interface): Required, focused on:
  - `runEmbeddedPiAgent` adapter boundary
  - TS host ↔ Python sidecar RPC contract
  - approval continuity and sidecar lifecycle
- Level 3 (wire/protocol/field): Skip for experimental scope

## Evidence
- `src/agents/pi-embedded-runner/run.ts` - `runEmbeddedPiAgent`, queueing, workspace resolution, model/auth/context-engine setup, retry loop, return shape usage
- `src/agents/pi-embedded-runner/run/params.ts` - `RunEmbeddedPiAgentParams`, full input fields
- `src/agents/pi-embedded-runner/types.ts` - `EmbeddedPiAgentMeta`, `EmbeddedPiRunMeta`, `EmbeddedPiRunResult`
- `src/agents/pi-embedded-runner/run/attempt.ts` - `runEmbeddedAttempt`, `createAgentSession`, `SessionManager.open`, `subscribeEmbeddedPiSession`, `createOpenClawCodingTools`, `resolveSandboxContext`
- `src/agents/pi-embedded-runner/lanes.ts` - `resolveSessionLane`, `resolveGlobalLane`
- `src/process/command-queue.ts` - `enqueueCommandInLane`, queue/lane concurrency behavior, gateway draining
- `src/agents/pi-embedded-runner/runs.ts` - active run registry, waiters, global singleton maps
- `src/agents/workspace-run.ts` - `resolveRunWorkspaceDir`
- `src/gateway/server-methods/agent.ts` - `agentHandlers.agent`, `dispatchAgentRunFromGateway`, gateway ingress path
- `src/agents/agent-command.ts` - `runAgentAttempt`, `agentCommandFromIngress`, ACP-vs-embedded dispatch, main caller path
- `src/auto-reply/reply/agent-runner-execution.ts` - `runAgentTurnWithFallback`, streaming/tool-result caller behavior
- `src/auto-reply/reply/followup-runner.ts` - `createFollowupRunner`, follow-up caller/result handling
- `src/auto-reply/reply/agent-runner-memory.ts` - `runMemoryFlushIfNeeded`, memory-triggered caller/result handling
- `src/cron/isolated-agent/run.ts` - `runCronIsolatedAgentTurn`, cron caller/result handling
- `src/commands/models/list.probe.ts` - `probeTarget`, auth probe caller behavior
- `src/hooks/llm-slug-generator.ts` - `generateSlugViaLLM`, slug caller behavior
- `extensions/voice-call/src/response-generator.ts` - `generateVoiceResponse`, voice caller behavior
- `extensions/llm-task/src/llm-task-tool.ts` - `createLlmTaskTool`, plugin caller behavior
- `src/plugins/runtime/runtime-agent.ts` - `createRuntimeAgent`, plugin runtime exposure
- `src/agents/pi-embedded-subscribe.handlers.tools.ts` - approval-pending reply emission, deterministic approval prompt flag
- `src/agents/pi-embedded-runner/run/payloads.ts` - `buildEmbeddedRunPayloads`, deterministic approval prompt suppression
- `src/agents/pi-embedded-subscribe.handlers.lifecycle.ts` - lifecycle event emission
- `src/agents/bash-tools.exec.ts` - host exec routing, pending approval return path
- `src/agents/bash-tools.exec-types.ts` - `ExecToolDetails`, `approval-pending` and `approval-unavailable` tool result shapes
- `src/agents/bash-tools.exec-host-gateway.ts` - gateway-host approval registration, async approval follow-up, pending tool result
- `src/agents/bash-tools.exec-host-node.ts` - node-host approval registration, async approval follow-up, pending tool result
- `src/agents/bash-tools.exec-approval-request.ts` - `registerExecApprovalRequest`, `waitForExecApprovalDecision`, gateway approval RPC calls
- `src/agents/bash-tools.exec-host-shared.ts` - `createAndRegisterDefaultExecApprovalRequest`, `buildExecApprovalPendingToolResult`, approval decision handling
- `src/gateway/server-methods/exec-approval.ts` - `createExecApprovalHandlers`, request/wait/resolve gateway methods
- `src/gateway/exec-approval-manager.ts` - `ExecApprovalManager`, in-memory pending approval map
- `src/config/sessions/types.ts` - `SessionEntry`, existing session persistence surface
- `src/config/types.agents.ts` - `AgentRuntimeConfig`, `AgentConfig`, existing runtime selection surface
- `src/config/zod-schema.agents.ts` - `AgentsSchema`, existing agent config schema entry point
- `src/config/types.openclaw.ts` - `OpenClawConfig`, top-level config surface
- `src/cli/gateway-cli/run-loop.ts` - `runGatewayLoop`, gateway lifecycle and restart drain behavior
- `src/process/supervisor/adapters/child.ts` - `createChildAdapter`, stdio child-process pattern
- `src/process/child-process-bridge.ts` - `attachChildProcessBridge`, signal forwarding pattern
- `package.json` - Node version, TypeScript version, external package dependencies
- `pyproject.toml` - Python tooling target only (`py310`)
