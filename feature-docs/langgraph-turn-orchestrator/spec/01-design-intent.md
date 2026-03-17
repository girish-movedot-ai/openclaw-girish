# LangGraph Turn-Orchestrator Replacement — Design Intent

## What This Document Is

This document describes **what we want to build and why**. It does NOT describe where code lives, what files to edit, or what existing types look like. Those facts must come from codebase exploration, not from this document.

If anything in this document contradicts what you find in the code, **the code wins**.

---

## Summary

Replace the agent-turn decision logic inside the existing embedded turn runner with a LangGraph-driven Python state machine. The replacement target is the **decision-making and control flow** — not the gateway, not the execution surfaces, not the approval UX.

The turn runner's public interface (function signature and return type) must remain identical. No caller should know which path ran.

---

## Architecture Principle: Graph Decides, Host Executes

The LangGraph state machine owns:
- Understanding the user's message in context
- Deciding what to do (respond, execute, ask clarification, escalate)
- Building a typed execution request when action is needed
- Verifying execution results
- Generating the reply

The TypeScript host owns:
- Process lifecycle of the Python sidecar
- Dispatching to legacy or LangGraph based on a feature flag
- Translating between existing TS types and the graph's RPC types
- Running all actual execution (shell, tools, plugins) through existing OpenClaw surfaces
- Handling approval UX through existing mechanisms
- Translating graph output back into the existing return type

The boundary between them is a small RPC interface with three commands:
- `invoke_turn` — start a new turn
- `resume_turn` — continue after host execution or approval
- `health` — liveness check

---

## State Machine Design

### Nodes

| Node | Purpose |
|------|---------|
| `ingest_turn` | Normalize incoming run parameters into graph state. Includes session identity, user message, recent transcript, workspace/channel context, approval resume payload if present. |
| `reconstruct_state` | Build a representation of what the agent knows about the current session/task state. |
| `diagnose_unknowns` | Identify what's missing - blocking unknowns vs. non-blocking. Output: unknowns list, blocking reason, confidence basis. |
| `decide_intent` | Single structured decision: `respond`, `execute`, `ask_clarification`, or `escalate`. No text generation here. |
| `build_execution_request` | For `execute` intent: emit a typed execution request for the TS host. Prefer deterministic templates when available; fall back to constrained shell when not. |
| `await_host_execution` | Graph interrupt point. The graph pauses. The TS host receives the execution request, runs it through existing OpenClaw surfaces (including approval), then resumes the graph with the result. |
| `verify_result` | Decide whether execution result is complete, failed, blocked, or needs one bounded retry. |
| `render_reply` | Generate final user-visible output from graph state and execution result. |
| `persist_turn_artifacts` | Persist any state needed for resume/approval continuity. |

### Terminal States

- `done` - turn completed normally
- `blocked_waiting_for_user` - awaiting clarification or approval
- `escalated` - safety escalation or refusal
- `failed` - unrecoverable error

### Routing Rules

```
ingest_turn → reconstruct_state → diagnose_unknowns → decide_intent

  respond          → render_reply → persist → done
  ask_clarification → render_reply → persist → blocked_waiting_for_user
  escalate         → render_reply → persist → escalated
  execute          → build_execution_request → await_host_execution
                     → (resume with result) → verify_result
                       → complete → render_reply → persist → done
                       → failed   → render_reply → persist → failed
                       → blocked  → render_reply → persist → blocked_waiting_for_user
                       → retry AND retry_count < 1 → diagnose_unknowns (re-enter)
                       → retry AND retry_count >= 1 → render_reply → persist → failed
```

**Hard constraint:** No open-ended self-reflection loop. `verify_result` may loop back to `diagnose_unknowns` exactly once. Enforce with a counter.

---

## Execution Intents (v1)

| Intent | Description | Available in v1? |
|--------|-------------|-----------------|
| `reply` | Direct text response, no execution | Yes |
| `shell` | Constrained shell command with approval + verification | Yes |
| `approval_request` | Request user approval before proceeding | Yes |
| `memory_write` | Write to agent memory | No - future |
| `email_send` | Send email | No - future |
| `http_call` | Make HTTP request | No - future |

For `shell` in v1:
- Approval requirement must be preserved
- Working directory bounds must be preserved
- Verification of output is required
- Failures must be rendered loudly (no silent swallowing)

---

## Sidecar Model

The Python LangGraph process runs as a **sidecar owned by the TS gateway process**. It is NOT an external network dependency.

Requirements:
- TS gateway starts and stops the sidecar
- Communication is local only (no public network exposure)
- Health check with timeout
- If sidecar is unavailable on a NEW turn: fall back to legacy (when flag policy allows)
- If sidecar fails MID-TURN: fail loudly. Do NOT fall back to legacy mid-turn. Do NOT invent success.
- On gateway shutdown: graceful stop with timeout, then force kill

---

## Feature Flag

A per-agent (or per-session) flag determines orchestration mode:
- `legacy` - existing turn runner (default)
- `langgraph` - new state machine

Resolution order: per-session override → per-agent config → global default.

Default is `legacy` until explicitly promoted. No feature flag system exists today - one must be created (simple is fine for experiment).

---

## RPC Wire Types (Conceptual)

These are the **logical data shapes** the RPC boundary needs to carry. The actual field names and types must be derived from whatever the existing turn runner's input and output types contain.

### Turn Request (TS → Python)
- Run/session identifiers
- Agent identity
- Latest user message
- Recent conversation history
- Workspace and channel context
- Tool/runtime capability snapshot
- Approval resume payload (null for new turns)
- Trace/diagnostics metadata

### Execution Request (Python → TS, on graph interrupt)
- Idempotency key
- Intent type (from the v1 intent table above)
- Typed action arguments
- Whether approval is required
- Verification contract (what success and failure look like)

### Execution Result (TS → Python, on graph resume)
- Status (completed, failed, approval_pending, cancelled)
- Whether execution actually ran
- Result payload (stdout/stderr, tool result, etc.)
- Any barrier/blocking condition
- Verification evidence

### Turn Response (Python → TS, on graph completion)
- Reply text
- Run metadata
- Pending approval descriptor (if blocked on approval)
- Terminal state (done, blocked, escalated, failed)
- Structured error (if failed)

**Critical:** These conceptual shapes must be reconciled with the actual existing types. Every field in the existing turn runner's input must map to something in the Turn Request. Every field in the existing return type must be producible from the Turn Response. No gaps.

---

## Approval as First-Class Resume

Approval continuation must be a proper resume path in the graph - not an ad hoc check. When a turn blocks on approval:

1. Graph persists enough state to resume
2. Graph returns a "blocked" response with an approval descriptor
3. TS host handles approval UX through existing mechanisms
4. When user approves, TS host calls `resume_turn` with the approval payload
5. Graph resumes from the interrupt point

This must survive sidecar restart when checkpoint state is available.

---

## Rollout Sequence

1. Wire up the adapter and feature flag. All traffic stays on legacy.
2. Make the graph handle `respond`, `ask_clarification`, and `escalate` end-to-end.
3. Add the host interrupt/resume execution path for `execute`.
4. Route one low-risk agent to LangGraph. Monitor.
5. Gradually promote additional agents.
6. Change default to `langgraph` once parity is confirmed.
7. Remove legacy runner only after sufficient confidence.

Rollback at any step: set the flag back to `legacy`.

---

## What Is Explicitly Out of Scope

- `runGatewayLoop` - the process lifecycle supervisor. Not touched.
- Gateway server or channel ingress. Not touched.
- Client-visible gateway APIs. Not touched.
- Approval UX. Not touched.
- Tool/plugin surfaces. Not touched.
- Session and workspace semantics. Not touched.
- Horizontal scaling of the sidecar.
- Deterministic action templates (shell-only is the v1 path).
- Multi-turn graph memory.
- Production rollout procedures.
- L3 wire-level security hardening.

---

## Test Scenarios (from design intent)

### Parity
- Existing callers of the turn runner work unchanged on both paths
- Session/workspace/channel context arrives in graph state and matches legacy inputs

### Behavior
- Direct response exits through `respond` without execution
- Clarification asks exactly one blocking question
- Escalation preserves current safety behavior
- Execution requests approval when required, resumes correctly, returns verified output
- Execution failure is surfaced as failure, not narrated as success
- Retry stops after exactly one re-diagnosis

### Reliability
- Sidecar unavailable → clear error + legacy fallback on new turns
- Sidecar crash mid-turn → loud failure, no fake completion
- Approval resume after restart → works when checkpoint exists
- Abort/cancel propagates cleanly

### Observability
- Graph path selected
- State reconstruction success/failure
- Intent chosen
- Execution request emitted
- Approval blocked/resumed
- Verification pass/fail
- Sidecar lifecycle events (start, crash, restart)
