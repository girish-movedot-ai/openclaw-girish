# User Stories: LangGraph Turn-Orchestrator Replacement

## Positive Stories

### US-001 - Operator routes an agent to LangGraph

**As** an internal operator  
**I want** to select the LangGraph orchestration path for a target agent or session  
**So that** I can test the new turn controller without changing caller code.

**Current-code grounding**

- Current caller contract is `runEmbeddedPiAgent(params): Promise<EmbeddedPiRunResult>`.
- The repo already has agent runtime routing for `embedded` vs `acp` in `src/config/types.agents.ts` - `AgentRuntimeConfig`.
- Current routing surface now adds `turnOrchestration?: "legacy" | "langgraph"` at defaults, per-agent, and per-session scope.

### US-002 - Turn completes through respond path

**As** a caller of `runEmbeddedPiAgent`  
**I want** a direct-response turn to finish without host execution  
**So that** simple reply turns still return normal `payloads` and `meta`.

**Current-code grounding**

- `EmbeddedPiRunResult` already supports text/media payloads plus run metadata.
- Existing callers in gateway, auto-reply, voice-call, and plugins already consume that result shape.

### US-003 - Turn completes through execute path with approval

**As** a caller of `runEmbeddedPiAgent`  
**I want** execution turns to reuse the existing host execution and approval surfaces  
**So that** the LangGraph path does not fork runtime behavior for shell/tool execution.

**Current-code grounding**

- Current exec approval goes through `bash-tools.exec*`, gateway approval handlers, and `ExecApprovalManager`.
- Current code already returns structured `approval-pending` tool results and deterministic approval reply payloads.

### US-004 - Turn completes through clarification path

**As** a caller of `runEmbeddedPiAgent`  
**I want** clarification turns to stop with a user-visible question instead of executing tools  
**So that** the session waits for user input before doing work.

**Current-code grounding**

- Current callers already handle normal reply payloads and empty/no-exec outcomes.
- `runEmbeddedPiAgent` already supports non-exec turns because reply generation is expressed through `payloads`, not through a required execution result type.

## Negative Stories

### NS-001 - Invalid orchestration mode does not route unpredictably

**As** an operator  
**I do not want** an unexpected orchestration value to silently route turns to the wrong engine  
**So that** rollout errors fail in a controlled way.

**Why this is required**

- The existing agent runtime selector is a strict union (`embedded` or `acp`).
- There is no current `langgraph` value, so adding one creates a new routing failure case.

### NS-002 - Sidecar unavailable on a new turn falls back to legacy

**As** an operator testing LangGraph  
**I do not want** a brand-new turn to hard-fail just because the sidecar is unavailable  
**So that** experiments can fail safe at the start of a turn.

**Why this is required**

- Current code has no sidecar and the embedded runner is the only proven path.
- The design intent requires new-turn fallback when policy allows.

### NS-003 - Mid-turn sidecar crash does not fall back to legacy

**As** an operator  
**I do not want** a turn that already entered LangGraph to jump back into legacy logic mid-flight  
**So that** the system does not mix two orchestration states in one turn.

**Why this is required**

- Current code keeps embedded run state in memory with active-run maps and per-turn retry state.
- Approval and follow-up behavior already shows that mid-turn state matters and is not stateless.

### NS-004 - Clarification path must not execute tools

**As** a caller  
**I do not want** a clarification turn to emit host execution requests  
**So that** “ask the user first” stays a true blocked turn.

**Why this is required**

- Current `EmbeddedPiRunResult` can already return user-visible payloads without any execution result field.

### NS-005 - Sidecar crash produces a clear failure

**As** an operator  
**I do not want** sidecar crashes to look like success or silent no-op turns  
**So that** debugging is immediate.

**Why this is required**

- Current code already emits lifecycle `error` events and structured runner errors for important failures.
- The LangGraph path must preserve that “fail loud” behavior.

## Story-to-Code Notes

- The best current adapter seam is the existing embedded runner boundary:
  - `src/agents/pi-embedded-runner/run.ts` - `runEmbeddedPiAgent`
  - `src/agents/pi-embedded-runner/run/params.ts` - `RunEmbeddedPiAgentParams`
  - `src/agents/pi-embedded-runner/types.ts` - `EmbeddedPiRunResult`
- The best current routing seam is the existing agent runtime selector:
  - `src/config/types.agents.ts` - `AgentRuntimeConfig`
- The biggest current mismatch with the target execute/approval story is approval continuation:
  - current code uses async follow-up messaging after `approval-pending`
  - current code does not resume the original turn through `runEmbeddedPiAgent`

## Evidence

- `feature-docs/langgraph-turn-orchestrator/spec/01-design-intent.md` - target stories implied by graph nodes, fallback rules, and approval resume requirements
- `src/agents/pi-embedded-runner/run.ts` - `runEmbeddedPiAgent`, current stable turn-runner boundary
- `src/agents/pi-embedded-runner/run/params.ts` - `RunEmbeddedPiAgentParams`, current input surface
- `src/agents/pi-embedded-runner/types.ts` - `EmbeddedPiRunResult`, current output surface
- `src/config/types.agents.ts` - `AgentRuntimeConfig`, current `embedded` / `acp` runtime selector
- `src/config/sessions/types.ts` - `SessionEntry`, lack of session-level LangGraph routing field in the inspected session state surface
- `src/agents/pi-embedded-runner/runs.ts` - active in-memory run state, showing mid-turn statefulness
- `src/agents/pi-embedded-subscribe.handlers.lifecycle.ts` - lifecycle `start` / `end` / `error` events
- `src/agents/bash-tools.exec-host-gateway.ts` - gateway approval registration, `approval-pending` return, async follow-up execution
- `src/agents/bash-tools.exec-host-node.ts` - node approval registration, `approval-pending` return, async follow-up execution
- `src/agents/pi-embedded-subscribe.handlers.tools.ts` - deterministic approval prompt emission from `approval-pending` tool results
