# Use-Case Brief: LangGraph Turn-Orchestrator Replacement

## Persona

- Internal developer/operator working on OpenClaw agent runtime behavior
- Needs to replace the current embedded turn control path without breaking existing callers, delivery flows, or result types

## Job-to-be-Done

Replace the decision-making and control flow inside the existing embedded turn runner with a LangGraph-driven state machine while preserving the current `runEmbeddedPiAgent(params): Promise<EmbeddedPiRunResult>` interface.

## Current Workflow (from codebase)

1. A caller constructs a `RunEmbeddedPiAgentParams` object and calls `runEmbeddedPiAgent`.
   - Main user/gateway path: `src/gateway/server-methods/agent.ts` -> `dispatchAgentRunFromGateway` -> `src/agents/agent-command.ts` - `agentCommandFromIngress` -> `runAgentAttempt`
   - Other direct callers include auto-reply, cron, memory flush, voice-call response generation, auth probes, and plugin/runtime helpers.
2. `runEmbeddedPiAgent` serializes work through a session lane and a global lane.
   - `src/agents/pi-embedded-runner/run.ts` uses `resolveSessionLane(params.sessionKey?.trim() || params.sessionId)` and `resolveGlobalLane(params.lane)`, then wraps execution in nested `enqueueCommandInLane` calls.
3. The runner resolves workspace, plugins, model, auth profile, and context engine before entering its retry loop.
   - `src/agents/pi-embedded-runner/run.ts` calls `resolveRunWorkspaceDir`, `ensureRuntimePluginsLoaded`, `resolveModelAsync`, `getApiKeyForModel`, `prepareProviderRuntimeAuth`, and `resolveContextEngine`.
4. Each loop iteration runs `runEmbeddedAttempt`.
   - `src/agents/pi-embedded-runner/run/attempt.ts` opens the session transcript with `SessionManager.open`, prepares the session, creates the embedded agent session with `createAgentSession`, subscribes to agent events with `subscribeEmbeddedPiSession`, and supplies tools via `createOpenClawCodingTools`.
5. The runner returns `EmbeddedPiRunResult`.
   - `payloads` carry user-visible text/media/error items.
   - `meta` carries duration, agent usage/model/session info, stop reason, and structured runner errors.
6. Approval today is not a true resume.
   - If exec approval is needed, the tool path returns an `approval-pending` tool result.
   - The subscribe layer emits a deterministic approval prompt.
   - The approval manager stores pending state in memory and a separate async follow-up later sends the approval outcome.

## Target Workflow (from plan)

This section is the target design, not current code.

1. TS host receives the same turn inputs that current callers already pass to `runEmbeddedPiAgent`.
2. TS host routes the turn to legacy embedded logic or the LangGraph path based on orchestration mode.
3. LangGraph sidecar runs the graph path:
   - `ingest_turn`
   - `reconstruct_state`
   - `diagnose_unknowns`
   - `decide_intent`
4. For `respond`, `ask_clarification`, or `escalate`, the graph renders the reply and the TS host converts it back to `EmbeddedPiRunResult`.
5. For `execute`, the graph emits an execution request, the TS host runs it through existing OpenClaw execution/approval surfaces, then resumes the graph with the execution result.
6. The graph verifies the result, renders the final reply, and the TS host returns the existing `EmbeddedPiRunResult` shape.

## Concrete Example

Example target turn:

1. Gateway user turn enters through `src/gateway/server-methods/agent.ts` and eventually reaches `runEmbeddedPiAgent`.
2. The caller-facing TypeScript signature stays unchanged.
3. The LangGraph path receives the real turn context already present in `RunEmbeddedPiAgentParams`, including:
   - session identity (`sessionId`, `sessionKey`, `sessionFile`)
   - workspace (`workspaceDir`)
   - user message (`prompt`)
   - delivery context (`messageChannel`, `messageProvider`, `messageTo`, `messageThreadId`)
   - sender/group context (`sender*`, `group*`)
4. The graph decides the turn needs execution.
5. The TS host runs the execution through the existing exec/approval flow instead of inventing a new executor.
6. If approval is required, the host should resume the graph after approval instead of using the current async follow-up pattern.
7. The final reply returns through the existing `EmbeddedPiRunResult.payloads` and `EmbeddedPiRunResult.meta`.

## System Boundaries

- LangGraph graph decides:
  - what the turn intends to do
  - whether execution is needed
  - whether execution succeeded
  - what final reply text to emit
- TS host executes:
  - process ownership for the sidecar
  - routing from caller to legacy vs LangGraph path
  - all existing tool/shell/plugin execution
  - all existing approval UX surfaces
  - translation between graph RPC shapes and `RunEmbeddedPiAgentParams` / `EmbeddedPiRunResult`
- Current-code mismatch to preserve:
  - gateway APIs stay the same
  - `runGatewayLoop` stays the same public lifecycle owner
  - `runEmbeddedPiAgent` signature and `EmbeddedPiRunResult` shape stay the same

## Scale Parameters (Experimental)

- One turn at a time per session should be preserved.
  - Current proof: `runEmbeddedPiAgent` queues on a per-session lane in `src/agents/pi-embedded-runner/run.ts`.
- Current gateway process is single-process in-memory for active embedded runs and approval state.
  - `src/agents/pi-embedded-runner/runs.ts` and `src/gateway/exec-approval-manager.ts` both use in-memory maps/global singletons.
- Target sidecar count from plan: one local sidecar process.
  - Current-code status: Gap. No Python sidecar exists yet.

## Failure Priority (for experimental scope)

1. Sidecar crash mid-turn must fail loudly and must not silently produce success.
2. RPC timeout must not hang the gateway turn path.
3. Bad graph state must not produce fake success.
4. Approval resume after restart is currently a real gap because current approval state is only in memory; if implemented, the new path must state exactly what persists and what does not.

## Evidence

- `feature-docs/langgraph-turn-orchestrator/spec/01-design-intent.md` - target workflow, graph nodes, interrupt/resume intent, failure priorities
- `src/gateway/server-methods/agent.ts` - `agentHandlers.agent`, `dispatchAgentRunFromGateway`
- `src/agents/agent-command.ts` - `agentCommandFromIngress`, `runAgentAttempt`
- `src/agents/pi-embedded-runner/run.ts` - `runEmbeddedPiAgent`, session/global queueing, workspace/model/auth/context setup, retry loop, return path
- `src/agents/pi-embedded-runner/run/params.ts` - `RunEmbeddedPiAgentParams`, current caller-facing input surface
- `src/agents/pi-embedded-runner/types.ts` - `EmbeddedPiRunResult`, current caller-facing output surface
- `src/agents/pi-embedded-runner/run/attempt.ts` - `runEmbeddedAttempt`, `SessionManager.open`, `createAgentSession`, `subscribeEmbeddedPiSession`, `createOpenClawCodingTools`
- `src/process/command-queue.ts` - `enqueueCommandInLane`, single-lane serialization behavior
- `src/agents/pi-embedded-runner/runs.ts` - active-run in-memory state
- `src/gateway/exec-approval-manager.ts` - approval pending state stored in memory
- `src/agents/bash-tools.exec-host-gateway.ts` - approval-pending result plus async follow-up execution
- `src/agents/bash-tools.exec-host-node.ts` - approval-pending result plus async follow-up execution
- `src/agents/pi-embedded-subscribe.handlers.tools.ts` - deterministic approval prompt emission from tool results
