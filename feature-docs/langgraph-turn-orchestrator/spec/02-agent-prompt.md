# PROMPT: LangGraph Turn-Orchestrator Replacement — Spec Discovery & Generation

## Your Role

You are a spec architect. Your job is to **explore the OpenClaw codebase**, then produce a specification package that a separate coding agent can implement in one shot.

You are NOT implementing anything. You are producing documents.

## Scope Context

This is an **experimental build**, not production-grade. The goal is a working prototype that:
- Correctly replaces the turn orchestrator with a LangGraph state machine
- Preserves all existing interfaces (callers don't break)
- Handles the obvious failure modes (sidecar crash, RPC timeout, bad state)
- Does NOT need full security hardening, L3 wire-level analysis, or adversarial review

But: the spec must be precise enough that the implementing agent **doesn't waste time debugging ambiguous requirements**. Every type, every interface, every state transition must be concrete - derived from what you find in the code, not invented.

## Priority Order

The deliverables must make one priority order unmistakable:

1. Passing the end-to-end success criteria is the top priority
2. Handling the required failure modes is the next priority
3. Preserving interfaces and mappings comes next
4. Remaining must-have requirements come after that

The implementation agent should optimize for observable end-to-end success through the real OpenClaw Gateway and browser UI, not for abstract completeness alone.

---

## Input Document

Read `feature-docs/langgraph-turn-orchestrator/spec/01-design-intent.md`. This document describes:
- What's being built and why
- The state machine design (nodes, routing, interrupt/resume)
- The "graph decides, host executes" boundary
- Conceptual RPC wire shapes
- Rollout strategy
- Test scenarios
- What's explicitly out of scope

The design intent document is authoritative for **what we want** - the desired behavior, the architecture principle, the state machine shape, the rollout strategy.

It is NOT authoritative for **what exists in the codebase**. It contains no file paths, no type names, no code references. Those must come exclusively from your codebase exploration.

## Proof Standard

Your job is to prove what exists in the codebase, not to write plausible documentation.

Treat these as hard rules:

1. Every non-trivial claim about the current system must be backed by code evidence.
2. Code evidence means a real repo path plus the symbol, type, function signature, or code block you used.
3. If you cannot prove a claim from the codebase, label it clearly as `Unknown` or `Gap`.
4. Do not fill missing details with "likely", "probably", "should", or "presumably".
5. Do not convert design-intent terms into claimed code facts unless you found the matching code.
6. If a section depends on an unverified fact, stop and record the missing evidence before continuing.

### Required Evidence Format

For every deliverable, include a final `## Evidence` section that lists the exact files and symbols used to support that document.

Minimum format:

```markdown
## Evidence
- `path/to/file.ts` - `symbolName`, why it matters
- `path/to/other-file.ts` - `OtherType`, fields copied from code
```

### Anti-Anchoring Rules

The design intent document uses working names like `runEmbeddedPiAgent`, `EmbeddedPiRunResult`, `GraphTurnRequest`, etc. These are **conceptual labels** - the actual function names, type names, and field names in the codebase may be completely different.

When you explore the codebase:
1. **Search by behavior, not by name.** Don't just grep for `runEmbeddedPiAgent`. Search for the turn runner entry point by looking for where agent turns are initiated - follow the call chain from the gateway.
2. **Map fields from code to concept, not concept to code.** Start with the actual type you find, list every field, THEN map each field to the conceptual wire shape. Do NOT start with the conceptual shape and look for matching fields.
3. **If the code doesn't have something the design intent expects, say so.** Don't invent it, don't approximate it. Flag it as a gap.
4. **If the code has something the design intent doesn't mention, include it.** The design intent is intentionally abstract - the code has the real complexity.

---

## Execution Flow

Run these phases in order. Do not skip phases. Save each deliverable before starting the next phase.

```
Phase 0:  Success Criteria                       → feature-docs/langgraph-turn-orchestrator/deliverables/00a-success-criteria.md
Phase 0.5: Codebase Discovery + System Profile  → feature-docs/langgraph-turn-orchestrator/deliverables/00b-system-profile.md
Phase 1:  Use-Case Brief (derive from plan)       → feature-docs/langgraph-turn-orchestrator/deliverables/01-use-case-brief.md
Phase 1.5: User Stories + Negative Stories        → feature-docs/langgraph-turn-orchestrator/deliverables/01b-user-stories.md
Phase 2:  Requirements (Must-haves only)          → feature-docs/langgraph-turn-orchestrator/deliverables/02-requirements.md
Phase 2.5: Runtime Constraints (bug-relevant)     → feature-docs/langgraph-turn-orchestrator/deliverables/02b-runtime-constraints.md
Phase 3:  Failure Analysis (L1 + targeted L2)     → feature-docs/langgraph-turn-orchestrator/deliverables/03-failure-register.md
Phase 4:  Implementation Spec                     → feature-docs/langgraph-turn-orchestrator/deliverables/04-implementation-spec.md
Phase 5:  Codex Handoff Prompt                    → feature-docs/langgraph-turn-orchestrator/deliverables/05-codex-handoff-prompt.md
```

Create `feature-docs/langgraph-turn-orchestrator/deliverables/` directory before starting.

---

## Phase 0: Success Criteria

Write `feature-docs/langgraph-turn-orchestrator/deliverables/00a-success-criteria.md` first.

This is the most important deliverable. The purpose of the rest of the spec package is to help a later implementation agent pass this document in a real end-to-end run.

The success criteria must:
- require a real Gateway run, not only unit tests
- require use of the browser-based Control UI
- require proof that the LangGraph path ran instead of legacy
- require at least one loud negative-case failure
- require the implementation agent to initialize OpenClaw locally if needed
- avoid any requirement to connect a messaging service

The implementation agent must be able to use this document as the primary acceptance target.

Add a final `## Evidence` section listing the exact files and symbols used.

---

## Phase 0.5: Codebase Discovery + System Profile

This is the most critical phase. Everything downstream depends on what you find here.

### Step 0a: Explore the Monorepo Structure

Run these commands and record the output:

```bash
# Top-level structure
find . -maxdepth 2 -type f -name "*.ts" -o -name "*.py" -o -name "*.json" -o -name "*.toml" | head -100
ls -la
cat package.json 2>/dev/null || echo "no package.json"
cat pyproject.toml 2>/dev/null || echo "no pyproject.toml"
```

Then explore deeper based on what you find. Your goal is to map:
- Where the TS gateway code lives
- Where the turn runner entry point is (the function that handles a single agent turn end-to-end)
- Where Python code lives (if any exists yet - it may not)
- Package versions (Node, TypeScript, Python, LangGraph if present)

### Step 0b: Extract Existing Interfaces

Find the turn runner by **behavior**, not by name. The design intent calls it `runEmbeddedPiAgent` - the actual name may differ.

**Start here - find the turn entry point:**
```bash
# Search for the concept, not the exact name
grep -r "embedded\|EmbeddedPi\|runAgent\|agentTurn\|turnRunner\|piAgent" --include="*.ts" -l | head -20
# Also search for the return type concept
grep -r "RunResult\|TurnResult\|AgentResult\|PiRunResult" --include="*.ts" -l | head -20
```

If these don't find it, broaden:
```bash
# Find where the gateway hands off to agent logic
grep -r "gateway\|Gateway" --include="*.ts" -l | head -20
# Then read those files and trace the call chain to the turn runner
```

Once you've found the turn runner entry point, extract:

1. **The turn runner function signature and its input/output types.** Read the file. Copy the full type definitions for the parameters type and the return type. Every field.

2. **All callers of the turn runner:**
   ```bash
   # Use the actual function name you found, not the conceptual name
   grep -r "ACTUAL_FUNCTION_NAME" --include="*.ts" -l
   ```
   For each caller, note: which module, what parameters it passes, what it does with the result.

3. **Approval handling:**
   ```bash
   grep -r "approval\|approve\|Approval" --include="*.ts" -l | head -20
   ```
   Trace the approval flow: how approvals are requested, how they block, how they resume.

4. **Session and workspace types:**
   ```bash
   grep -r "sessionKey\|SessionKey\|workspace\|WorkspaceContext\|channelContext" --include="*.ts" -l | head -20
   ```

5. **The gateway loop:**
   ```bash
   grep -r "runGatewayLoop\|gatewayLoop" --include="*.ts" -l
   ```
   Read it to understand the process lifecycle (this is OUT OF SCOPE for changes but you need to understand how the sidecar will fit).

6. **Existing process management patterns:**
   ```bash
   grep -r "child_process\|spawn\|fork\|exec\|ChildProcess" --include="*.ts" -l | head -10
   ```

7. **Existing LangGraph / Python code (if any):**
   ```bash
   find . -name "*.py" -not -path "*/node_modules/*" | head -20
   grep -r "langgraph\|StateGraph\|state_reconstructor" --include="*.py" -l 2>/dev/null
   ```

### Step 0c: Produce System Profile

Write `feature-docs/langgraph-turn-orchestrator/deliverables/00b-system-profile.md` with this structure:

```markdown
# System Profile: LangGraph Turn-Orchestrator Replacement

## 1) Feature Classification
- Feature type: Internal infrastructure refactor (turn orchestrator replacement)
- Runtime mode: [what you found - sync interactive? hybrid?]
- Production exposure: [what you found - internal-only for now / customer-facing]
- Data sensitivity: [based on what flows through the turn runner]
- Blast radius if wrong: [High - breaks all agent turns on affected agents]

## 2) Dependency Topology

### Internal Dependencies
[List every module/service the turn runner touches, from your grep results]

### External Dependencies
[LLM API calls, any external services the runner uses]

## 3) Trust Boundaries
[From your codebase exploration - which boundaries exist?]

## 4) State and Concurrency Surface
[What shared mutable state did you find? Session state? Approval tokens?]

## 5) Key Interfaces (Extracted from Code)

### Turn Runner Entry Point
[Paste the actual function signature - whatever you found it's called]

### Turn Runner Input Type
[Paste the actual type definition - every field, actual name]

### Turn Runner Output Type
[Paste the actual type definition - every field, actual name]

### Caller Inventory
[Table: caller module | parameters used | result handling]

### Approval Flow
[Step-by-step trace of how approval works today]

## 6) Infrastructure Facts
- Node version: [from package.json engines or .nvmrc]
- TypeScript version: [from package.json]
- Python version: [if found]
- LangGraph version: [if found, likely not present yet]
- Deployment: [what you can infer]
- IPC patterns: [what exists in the codebase]
- Feature flag system: [none - will need to create]

## 7) FMEA Depth Target
- Level 1 (workflow/system): Required
- Level 2 (component/interface): Required - targeted at sidecar boundary and RPC layer
- Level 3 (wire/protocol/field): Skip for experimental scope

## Evidence
[List the exact files and symbols used in this document]
```

**GATE 0: Do not proceed until the system profile is saved and you have the actual type signatures of the turn runner's input and output from the codebase. If you cannot find the turn runner entry point, STOP and report what you found instead. Do not guess.**

---

## Phase 1: Use-Case Brief

Derive this from the architecture plan + what you found in the codebase. Do not interview the user.

Write `feature-docs/langgraph-turn-orchestrator/deliverables/01-use-case-brief.md`:

```markdown
# Use-Case Brief: LangGraph Turn-Orchestrator Replacement

## Persona
- Internal developer/operator at MOVEdot AI
- Needs to experiment with LangGraph-driven turn control without breaking existing agent behavior

## Job-to-be-Done
Replace the decision-making and control flow inside the agent turn runner with a structured state machine, while preserving all caller interfaces.

## Current Workflow (from codebase)
[Describe what happens today when the turn runner is called - based on your code reading]

## Target Workflow (from plan)
[Describe the LangGraph path: ingest → reconstruct → diagnose → decide → execute/reply]

## Concrete Example
[One specific turn: user sends "run the telemetry comparison script", the graph decides to execute, TS host runs it with approval, graph verifies, reply is generated]

## System Boundaries
- LangGraph graph DECIDES (what to do, whether to execute, whether result is valid)
- TS host EXECUTES (tools, shell, approval UX - through existing OpenClaw surfaces)
- The boundary is the RPC layer (invoke_turn / resume_turn)

## Scale Parameters (Experimental)
- One turn at a time per session
- Single sidecar process
- No horizontal scaling for v1

## Failure Priority (for experimental scope)
1. Sidecar crash mid-turn → must fail loudly, not silently
2. RPC timeout → must not hang the gateway
3. Bad graph state → must not produce fake success
4. Approval resume after restart → nice to have, not blocking

## Evidence
[List the exact files and symbols used in this document]
```

---

## Phase 1.5: User Stories

Write `feature-docs/langgraph-turn-orchestrator/deliverables/01b-user-stories.md`. Include negative stories for each positive story.

Must-have stories:
- Operator routes an agent to LangGraph via feature flag
- Turn completes through respond path (no execution)
- Turn completes through execute path (with approval)
- Turn completes through clarification path
- Sidecar crash produces clear failure (negative)
- Legacy fallback activates when sidecar unavailable on new turn (negative)
- Mid-turn sidecar crash does NOT fall back to legacy (negative)

Add a final `## Evidence` section listing the exact files and symbols used.

---

## Phase 2: Requirements

Write `feature-docs/langgraph-turn-orchestrator/deliverables/02-requirements.md`. **Must-haves only.** Derive from stories.

Use this schema:

```markdown
| ID | Requirement | Source | Testable Criterion | Priority |
|----|-------------|--------|-------------------|----------|
| FR-001 | ... | Story X | ... | Must |
```

Categories:
- FR: Functional (state machine behavior, routing, dispatch)
- IR: Interface (type preservation, RPC contract, adapter translation)
- FHR: Failure handling (sidecar crash, timeout, bad state)
- NFR: Non-functional (observability, flag-based routing)

**Skip Should and Deferred categories entirely.** Only Must-haves.

Traceability: every requirement must trace to a user story. Every story must have at least one requirement.

Add a final `## Evidence` section listing the exact files and symbols used.

---

## Phase 2.5: Runtime Constraints

Write `feature-docs/langgraph-turn-orchestrator/deliverables/02b-runtime-constraints.md`. Focus ONLY on constraints that cause debugging pain if unspecified.

Required sections:

### 1) Execution Model
- Is the TS gateway async (Node event loop)? Confirm from code.
- The Python sidecar is a separate process. What's the IPC model? (Based on what patterns exist in the codebase.)

### 2) Sidecar Lifecycle
- How is it started? (child_process.spawn? fork?)
- How is it stopped? (SIGTERM → wait → SIGKILL)
- What happens on crash? (detect via exit event, log, mark unavailable)
- Restart policy: [one retry on new turn, no retry mid-turn]

### 3) RPC Timeouts
- `invoke_turn` timeout: [propose a value based on current turn latency you observe in the code]
- `resume_turn` timeout: [same]
- `health` timeout: 2s (fixed)

### 4) Resource Budgets
- Max graph state size: [propose based on what the turn runner's input type contains]
- Sidecar memory: [not enforced for experiment, document as unbounded]

### 5) Observability
- Required log fields: traceId, agentId, sessionId, orchestrationMode, graphNode, intent, terminalState
- Required events: sidecar_start, sidecar_crash, sidecar_restart, turn_start, turn_complete, turn_failed, fallback_to_legacy

Add a final `## Evidence` section listing the exact files and symbols used.

---

## Phase 3: Failure Analysis

Write `feature-docs/langgraph-turn-orchestrator/deliverables/03-failure-register.md`. L1 (workflow) + targeted L2 (sidecar boundary, RPC, state).

### Taxonomy Applicability

| Category | Applicable? | Reason |
|----------|-------------|--------|
| C1 Identity/Access | No | No auth changes - existing session identity flows through |
| C2 Config/Environment | Yes | Feature flag, sidecar startup, Python environment |
| C3 State Machine/Concurrency | Yes | Graph state transitions, retry logic, approval resume |
| C4 Contract/Boundary | Yes | RPC types, TS↔Python serialization, adapter translation |
| C5 Data/Artifact Pipeline | No | No new data pipelines |
| C6 Security/Privacy | No | No new trust boundaries for experiment |
| C7 Reliability/Observability | Yes | Sidecar lifecycle, timeout handling, failure reporting |

### FMEA Register

Use this schema:

```markdown
| FM ID | Level | Category | Trigger | Effect | Severity (1-10) | Mitigation | Test Case |
|-------|-------|----------|---------|--------|-----------------|------------|-----------|
```

**Mandatory rows** (you must analyze these - they are the top bug sources):

**L1 - Workflow:**
- FM-L1-01: Sidecar not running when turn starts
- FM-L1-02: Sidecar crashes mid-turn (after invoke, before response)
- FM-L1-03: Turn hangs (RPC never returns)
- FM-L1-04: Graph produces wrong intent (e.g., executes when it should ask)
- FM-L1-05: Execution succeeds but verify_result wrongly says failed
- FM-L1-06: Retry loop exceeds bound (retry_count not enforced)

**L2 - Component/Interface:**
- FM-L2-01: Turn runner input → GraphTurnRequest mapping drops a field
- FM-L2-02: GraphTurnResponse → Turn runner output mapping drops a field
- FM-L2-03: Approval resume payload doesn't round-trip through graph state
- FM-L2-04: Sidecar process exits with non-zero but TS doesn't detect it
- FM-L2-05: JSON serialization of graph state fails (circular refs, BigInt, undefined)
- FM-L2-06: Feature flag returns unexpected value (not 'legacy' or 'langgraph')
- FM-L2-07: Graph interrupt returns execution request but TS adapter doesn't recognize the intent type
- FM-L2-08: Two concurrent turns on the same session hit the same sidecar (if sessions can overlap)

For each row, propose:
- A concrete mitigation (code-level, not hand-wavy)
- A concrete test case name and description

Add a final `## Evidence` section listing the exact files and symbols used.

---

## Phase 4: Implementation Spec

Write `feature-docs/langgraph-turn-orchestrator/deliverables/04-implementation-spec.md`. This is the authoritative spec for the implementing agent.

### Required Sections:

**1) Mission** - One paragraph. What's being built, for whom, what this version delivers.

**2) System Responsibilities**
- LangGraph sidecar (Python): owns turn supervision, intent decision, verification
- TS host adapter: owns dispatch, execution, approval pass-through, type translation
- Feature flag resolver: owns routing decision
- Sidecar process manager: owns Python process lifecycle

**3) Component Specifications** - For each component:
- Exact file location (based on codebase conventions you discovered)
- Interface (function signatures with real types from the codebase)
- Behavior rules
- Error handling

**4) State Machine Definition** - The full graph with:
- State schema (Python TypedDict - use actual field types based on what you found in the turn runner's input type)
- Node list with inputs/outputs
- Routing rules (exhaustive, no ambiguity)
- Retry bound (exactly 1)

**5) RPC Contract** - Exact request/response shapes for invoke_turn, resume_turn, health. Use real types.

**6) Type Mapping Tables**
- Turn runner input type field → `GraphTurnRequest` field (every field, no gaps)
- `GraphTurnResponse` field → Turn runner output type field (every field, no gaps)
- If a field can't be mapped, explain why and what to do about it.

**7) Failure-Mode Matrix** - Import from Phase 3, add spec section references.

**8) Test Matrix** - Every test from Phase 3 + parity tests from the plan.

**9) Non-Goals (Explicit)**
- No L3 wire-level hardening
- No horizontal scaling
- No production rollout procedures
- No deterministic action templates (shell-only for v1)
- No multi-turn graph memory
- `memory_write`, `email_send`, `http_call` intents are NOT implemented

**10) Discovery Questions for Implementing Agent** - Anything your exploration couldn't answer. Be specific:
  - "I found X in file Y but couldn't determine Z - check [specific thing]."
  - NOT vague questions like "how does the system work?"

**11) Rollout Sequence** - From the plan, adapted for experimental scope.

**12) Traceability**
- Every requirement from Phase 2 → spec section
- Every failure mode from Phase 3 → spec section + test case

**13) Evidence**
- List the exact files and symbols used in this document

---

## Phase 5: Codex Handoff Prompt

Write `feature-docs/langgraph-turn-orchestrator/deliverables/05-codex-handoff-prompt.md`. This is what gets pasted into a fresh Claude Code instance.

Use this structure:

```markdown
# PROMPT: LangGraph Turn-Orchestrator - Implementation

## Context
You have these spec documents (read in this order):
1. `feature-docs/langgraph-turn-orchestrator/deliverables/04-implementation-spec.md` - Implementation spec (authoritative)
2. `feature-docs/langgraph-turn-orchestrator/deliverables/03-failure-register.md` - Failure register (every entry must be addressed)
3. `feature-docs/langgraph-turn-orchestrator/deliverables/02-requirements.md` - Requirements with traceability
4. `feature-docs/langgraph-turn-orchestrator/deliverables/01-use-case-brief.md` - Use-case context
5. `feature-docs/langgraph-turn-orchestrator/spec/01-design-intent.md` - Design intent (context only - spec overrides where they conflict)

## Your Task

### Phase 1: Verify Discovery
Read `feature-docs/langgraph-turn-orchestrator/deliverables/00b-system-profile.md` Section 5 (Key Interfaces). Verify the type signatures are still accurate. If they've changed, update the spec before proceeding.

[List specific discovery questions from spec Section 10]

### Phase 2: Implementation
[Component build order, parallel execution plan, execution rules]

### Phase 3: Verification
[Test commands, acceptance checks]

## Output Requirements
[Required files, final report format, traceability tables]

## Hard Rules
1. Do NOT change the runEmbeddedPiAgent signature or EmbeddedPiRunResult type.
2. Do NOT change runGatewayLoop.
3. Do NOT expose the sidecar on a public network endpoint.
4. Do NOT fall back to legacy mid-turn.
5. Do NOT invent success. Failures must be loud.
6. Do NOT allow more than 1 retry in verify_result.
7. Default orchestration mode is 'legacy'.
```

Fill in the bracketed sections with concrete details from your spec.

---

## Hard Rules for YOU (the spec agent)

1. **Never invent types.** Every TypeScript type you reference must come from the actual codebase. If you can't find it, say so in the discovery questions.
2. **Never assume file paths.** Use grep/find to locate files. The design intent document contains NO file paths - that's intentional.
3. **Every interface mapping must be field-by-field.** No "and other fields" or "etc." - list every field or flag it as unknown.
4. **If the codebase contradicts the design intent, the codebase wins.** Note the contradiction in the spec.
5. **Gate 0 is hard.** If you can't find the turn runner entry point or its types, STOP and report what you found. Do not proceed with invented types.
6. **Map code → concept, never concept → code.** When building type mapping tables, start from actual fields in codebase types and map each to the conceptual RPC shape. Do NOT start from the conceptual shape and look for matching fields.
7. **Names from the design intent are labels, not grep targets.** `runEmbeddedPiAgent`, `GraphTurnRequest`, `sessionKey` - these are conceptual. The real names come from the code.

## Output Checklist

Before declaring done, verify:
- [ ] `feature-docs/langgraph-turn-orchestrator/deliverables/00b-system-profile.md` has actual type signatures from the codebase
- [ ] `feature-docs/langgraph-turn-orchestrator/deliverables/01-use-case-brief.md` describes the real current workflow (not assumed)
- [ ] `feature-docs/langgraph-turn-orchestrator/deliverables/01b-user-stories.md` has negative stories for every positive story
- [ ] `feature-docs/langgraph-turn-orchestrator/deliverables/02-requirements.md` has only Must-haves, all traceable to stories
- [ ] `feature-docs/langgraph-turn-orchestrator/deliverables/02b-runtime-constraints.md` has concrete timeout values and lifecycle rules
- [ ] `feature-docs/langgraph-turn-orchestrator/deliverables/03-failure-register.md` covers all mandatory FM rows with mitigations and test cases
- [ ] `feature-docs/langgraph-turn-orchestrator/deliverables/04-implementation-spec.md` has field-by-field type mapping tables
- [ ] `feature-docs/langgraph-turn-orchestrator/deliverables/05-codex-handoff-prompt.md` is self-contained and references all other deliverables
- [ ] Every deliverable ends with an `Evidence` section listing exact files and symbols
- [ ] Any unproven claim is marked `Unknown` or `Gap`
- [ ] No TBD, no "etc.", no invented types anywhere in the deliverables
