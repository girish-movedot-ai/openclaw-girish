# LangGraph Turn Orchestrator Architecture

This is a temporary visualization of the architecture described by the current feature docs.

## Big Picture

The browser UI sends a prompt to the OpenClaw Gateway.

The Gateway still calls the same TypeScript entry point:

- `runEmbeddedPiAgent(params)`

That entry point now chooses between:

- `legacy`
- `langgraph`

If `langgraph` is selected, TypeScript does host-side work and the Python LangGraph sidecar does the turn orchestration.

## System Diagram

```mermaid
flowchart TD
    A[Browser Control UI] --> B[Gateway]
    B --> C[runEmbeddedPiAgent(params)]
    C --> D{turnOrchestration}

    D -->|legacy| E[Existing embedded runner]
    D -->|langgraph| F[TS LangGraph adapter]

    F --> G[Sidecar manager]
    G --> H[Python LangGraph sidecar]

    H --> I[ingest_turn]
    I --> J[reconstruct_state]
    J --> K[diagnose_unknowns]
    K --> L[decide_intent]

    L -->|respond| M[render_reply]
    L -->|ask_clarification| M
    L -->|escalate| M
    L -->|execute| N[build_execution_request]

    N --> O[interrupt back to TS host]
    O --> P[existing OpenClaw exec and approval surfaces]
    P --> Q[resume_turn]
    Q --> R[verify_result]

    R -->|complete| M
    R -->|blocked| M
    R -->|failed| M
    R -->|retry once| K

    M --> S[persist_turn_artifacts]
    S --> T[final EmbeddedPiRunResult]
    E --> T
```

## Responsibility Split

### TypeScript host owns

- selecting `legacy` vs `langgraph`
- starting and stopping the Python sidecar
- translating current TS input/output types
- performing all real execution through existing OpenClaw surfaces
- handling approval UX

### Python LangGraph sidecar owns

- understanding the turn
- reconstructing state
- deciding intent
- requesting execution when needed
- verifying execution results
- producing the final reply

## Turn Flow

### 1. Prompt enters OpenClaw

The browser UI sends a prompt to the Gateway.

The Gateway calls:

- `runEmbeddedPiAgent(params)`

### 2. Routing decision happens

TypeScript decides:

- `legacy` -> use the current embedded runner
- `langgraph` -> use the new TS adapter + Python sidecar path

### 3. LangGraph starts the turn

If `langgraph` is selected:

1. TS adapter builds a serializable graph request
2. TS sends `invoke_turn` to the Python sidecar
3. Python runs the graph nodes in order

### 4. The graph decides what kind of turn this is

The graph can choose:

- `respond`
- `ask_clarification`
- `escalate`
- `execute`

### 5. If execution is needed

The graph does not execute tools directly.

Instead:

1. Python emits an execution request
2. TS host receives it
3. TS uses the existing OpenClaw execution and approval system
4. TS sends the result back with `resume_turn`
5. Python verifies the result

### 6. Final reply is returned

After verification, Python renders the final reply and TS maps it back into:

- `EmbeddedPiRunResult`

This keeps callers unchanged.

## Mental Model

Think of it like this:

- TypeScript is the `driver and operator`
- LangGraph is the `brain for turn flow`
- Existing OpenClaw execution surfaces are still the `hands`

So the rule is:

- `Graph decides, host executes`

## What To Look For In Logs

If LangGraph is active, you should expect to see signs like:

- LangGraph selected
- sidecar started
- turn start
- turn complete

If only the legacy path is active, you should see embedded-runner behavior without sidecar activity.
