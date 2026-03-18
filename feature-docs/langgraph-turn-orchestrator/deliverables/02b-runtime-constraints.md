# Runtime Constraints: LangGraph Turn-Orchestrator Replacement

## 1) Execution Model

- The current TS gateway/runner is asynchronous Node.js code.
  - `src/agents/pi-embedded-runner/run.ts` - `runEmbeddedPiAgent` is `async` and returns `Promise<EmbeddedPiRunResult>`.
  - `src/cli/gateway-cli/run-loop.ts` - `runGatewayLoop` is `async` and owns gateway lifecycle.
  - `src/process/command-queue.ts` - queued turn execution is promise-based and lane-serialized.
- One turn at a time per session is the current rule.
  - `src/agents/pi-embedded-runner/run.ts` resolves a per-session lane and enqueues the turn there.
- The current embedded runner keeps important turn behavior in-process.
  - Model resolution, auth, context engine, transcript/session handling, and tool execution all happen in the TS process today.
- There is no existing Python orchestrator or Python RPC layer in the repo.
  - Repo search found no LangGraph runtime code outside the feature spec files.
- Existing local process-management code favors child-process stdio streams.
  - `src/process/supervisor/adapters/child.ts` - `createChildAdapter` exposes `stdin`, `onStdout`, `onStderr`, `wait`, `kill`
  - `src/process/child-process-bridge.ts` - `attachChildProcessBridge` forwards process signals to a child

**Specified constraint for the new sidecar**
- Use a local child process owned by the TS host.
- Use local-only stdio JSON messaging for the TS↔Python bridge.
- Do not use a public network listener.

**Why this is the least-guessy choice**
- It matches the existing child-process management style already present in the repo.
- No existing Python transport convention exists to reuse instead.

## 2) Sidecar Lifecycle

### Start

- Current-code anchor:
  - `src/process/supervisor/adapters/child.ts` shows the repo’s standard spawn/wait/kill shape.
- Specified rule:
  - Start the sidecar from the TS host before the first LangGraph turn that needs it.
  - Capture stdout/stderr for diagnostics.
  - Register exit/error listeners immediately.

### Stop

- Current-code anchors:
  - `src/process/child-process-bridge.ts` - signal forwarding
  - `src/cli/gateway-cli/run-loop.ts` - gateway shutdown uses a 5_000 ms shutdown timeout
- Specified rule:
  - On gateway shutdown, send `SIGTERM`, wait up to 5_000 ms, then force `SIGKILL` if still alive.

### Crash handling

- Current-code anchor:
  - `src/process/supervisor/adapters/child.ts` exposes child `wait()` and kill behavior; crashes are detectable through exit/error.
- Specified rule:
  - If the sidecar exits or errors before a new turn is invoked, mark it unavailable and apply new-turn fallback policy.
  - If it exits or errors after a LangGraph turn already started, fail that turn loudly and do not switch engines mid-turn.

### Restart policy

- Specified rule from the feature spec:
  - At most one automatic restart attempt before a **new** LangGraph turn.
  - No automatic restart or legacy fallback after a turn has already entered LangGraph mode.

### Current-state mismatch to record

- The current approval system is not restart-safe.
  - `src/gateway/exec-approval-manager.ts` stores pending approvals in memory only.
- Therefore, approval resume after restart is a new requirement, not a current property.

## 3) RPC Timeouts

### `invoke_turn`

- Current-code anchors:
  - `src/agents/timeout.ts` - full agent-turn default is 600 seconds (`DEFAULT_AGENT_TIMEOUT_SECONDS = 600`)
  - `src/gateway/node-registry.ts` uses a default `timeoutMs` of `30_000` for node RPC
  - `src/gateway/server-methods/agent.ts` - `agent.wait` defaults to `30_000`
- Specified timeout:
  - `invoke_turn` timeout = **30_000 ms**
- Reason:
  - This keeps sidecar RPC bounded like current gateway RPC patterns instead of inheriting the much larger full-turn timeout budget.
  - Full end-to-end turn timeout still stays under the existing host-side agent timeout budget.

### `resume_turn`

- Current-code anchors:
  - Same as `invoke_turn`
- Specified timeout:
  - `resume_turn` timeout = **30_000 ms**
- Reason:
  - Approval/execution resumes should be bounded by the same local RPC expectation as initial graph invocation.

### `health`

- Specified timeout:
  - `health` timeout = **2_000 ms**

## 4) Resource Budgets

### Max graph state size

- Current-code anchors:
  - `RunEmbeddedPiAgentParams` includes large fields like `prompt`, `images`, `skillsSnapshot`, `streamParams`, and many context fields.
  - `src/gateway/server-methods/agent.ts` - `parseMessageWithAttachments(... maxBytes: 5_000_000)` is the clearest current inbound payload cap tied to agent input preparation.
- Gap:
  - The current embedded runner has no explicit serialized-state size cap because it stays in-process.
- Specified budget:
  - Max serialized graph request size = **5 MiB**
- Required adapter behavior:
  - Do not send host-only callback fields, `AbortSignal`, `enqueue`, or mutable refs over RPC.
  - Reject oversize serialized requests explicitly instead of truncating silently.

### Sidecar memory

- Specified budget:
  - **Unbounded for the experiment**
- Required behavior:
  - No memory hard-limit enforcement is required in v1, but crashes/OOM exits must be detected and logged as sidecar failures.

## 5) Observability

### Required correlation fields

- Use current `runId` as the trace key source.
  - Current-code anchors:
    - `src/agents/pi-embedded-runner/run.ts` logs by `runId`, `sessionId`
    - `src/agents/pi-embedded-subscribe.handlers.lifecycle.ts` emits lifecycle events keyed by `runId`
- Required fields for the new path:
  - `traceId` (use existing `runId`)
  - `agentId`
  - `sessionId`
  - `orchestrationMode`
  - `graphNode`
  - `intent`
  - `terminalState`

### Required events

- Current-code anchors:
  - `src/agents/pi-embedded-subscribe.handlers.lifecycle.ts` - lifecycle `start` / `end` / `error`
  - `src/agents/pi-embedded-subscribe.handlers.tools.ts` - tool `start` / `update` / `end`
  - `src/agents/pi-embedded-subscribe.handlers.compaction.ts` - compaction `start` / `end`
- Required new-path events:
  - `sidecar_start`
  - `sidecar_crash`
  - `sidecar_restart`
  - `turn_start`
  - `turn_complete`
  - `turn_failed`
  - `fallback_to_legacy`

### Required behavior

- LangGraph mode must keep current lifecycle/error visibility instead of hiding failures inside the sidecar.
- Mid-turn crashes, timeout failures, and resume failures must all emit an explicit failure event and structured failure output.

## Evidence
- `src/agents/pi-embedded-runner/run.ts` - `runEmbeddedPiAgent`, queueing, runId/sessionId logging, current full-turn execution path
- `src/agents/pi-embedded-runner/run/params.ts` - `RunEmbeddedPiAgentParams`, current input surface and large/non-serializable fields
- `src/agents/pi-embedded-subscribe.handlers.lifecycle.ts` - lifecycle event emission
- `src/agents/pi-embedded-subscribe.handlers.tools.ts` - tool event and approval-pending handling
- `src/agents/pi-embedded-subscribe.handlers.compaction.ts` - compaction event emission
- `src/process/command-queue.ts` - promise-based lane serialization
- `src/process/supervisor/adapters/child.ts` - `createChildAdapter`, stdio child-process pattern
- `src/process/child-process-bridge.ts` - `attachChildProcessBridge`, signal forwarding
- `src/cli/gateway-cli/run-loop.ts` - `runGatewayLoop`, shutdown timeout and lifecycle ownership
- `src/agents/timeout.ts` - `resolveAgentTimeoutMs`, `DEFAULT_AGENT_TIMEOUT_SECONDS`
- `src/gateway/server-methods/agent.ts` - `agent.wait` timeout default, attachment parse maxBytes
- `src/gateway/node-registry.ts` - default node RPC timeout
- `src/gateway/exec-approval-manager.ts` - in-memory approval state
- `feature-docs/langgraph-turn-orchestrator/spec/01-design-intent.md` - required health timeout and restart/no-mid-turn-fallback intent
