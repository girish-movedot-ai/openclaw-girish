# PROMPT: LangGraph Turn-Orchestrator - Implementation

## Context

You have these spec documents (read in this order):
1. `feature-docs/langgraph-turn-orchestrator/deliverables/00a-success-criteria.md` - Highest-priority acceptance target
2. `feature-docs/langgraph-turn-orchestrator/deliverables/04-implementation-spec.md` - Implementation spec (authoritative after success criteria)
3. `feature-docs/langgraph-turn-orchestrator/deliverables/03-failure-register.md` - Failure register (every entry must be addressed)
4. `feature-docs/langgraph-turn-orchestrator/deliverables/02-requirements.md` - Requirements with traceability
5. `feature-docs/langgraph-turn-orchestrator/deliverables/01-use-case-brief.md` - Use-case context
6. `feature-docs/langgraph-turn-orchestrator/spec/01-design-intent.md` - Design intent (context only - spec overrides where they conflict)

## Your Task

### Phase 1: Verify Discovery

Read `feature-docs/langgraph-turn-orchestrator/deliverables/00b-system-profile.md` Section 5 (Key Interfaces). Verify the type signatures are still accurate. If they've changed, update the spec before proceeding.

Resolve these discovery questions before coding:
1. Python sidecar package location is a `Gap`; the repo has no product-runtime Python package convention.
2. `EmbeddedPiRunResult.meta.systemPromptReport` has no proven LangGraph parity path yet.
3. `EmbeddedPiRunResult` has no dedicated pending-approval descriptor field.
4. `EmbeddedPiRunResult.meta.pendingToolCalls` has no proven v1 shell-only LangGraph equivalent.
5. `didSendViaMessagingTool`, `messagingToolSent*`, and `successfulCronAdds` are host-side side-effect fields with no proven v1 shell-only mapping.
6. Session-level orchestration override storage location is not implemented; `SessionEntry` is the most obvious current seam but not yet confirmed.

If any of the verified code has changed enough to invalidate the mapping tables, update:
- `feature-docs/langgraph-turn-orchestrator/deliverables/00b-system-profile.md`
- `feature-docs/langgraph-turn-orchestrator/deliverables/04-implementation-spec.md`

### Phase 2: Implementation

Build in this order:

1. **Routing/config layer**
   - Add orchestration-mode config/session override plumbing.
   - Keep default mode `legacy`.
   - Do not overload ACP runtime selection.

2. **TS sidecar manager**
   - Add the child-process owner for the Python sidecar.
   - Use local-only stdio JSON messaging.
   - Add health, invoke, resume, and stop behavior.

3. **TS LangGraph adapter**
   - Preserve `runEmbeddedPiAgent(params): Promise<EmbeddedPiRunResult>`.
   - Add exhaustive request/response mapping.
   - Keep host-only callbacks and abort handling local to TS.

4. **Python graph**
   - Implement the state machine from the spec.
   - Start with `respond`, `ask_clarification`, and `escalate`.
   - Then add shell-only `execute` with host interrupt/resume.

5. **Approval/resume**
   - Replace the current async follow-up-only completion model for LangGraph turns with real `resume_turn` continuation.
   - Make restart behavior explicit: resume when checkpoint exists, fail explicitly when it does not.

6. **Failure and observability**
   - Add timeout handling, child exit detection, invalid-mode handling, and required logs/events.

Execution rules:
- Reuse existing host execution/approval surfaces.
- Preserve session-lane serialization.
- Fail closed on unknown RPC statuses or intent values.
- Add tests alongside each component change.

### Phase 3: Verification

Run targeted verification for:

- Success criteria first:
  - initialize OpenClaw locally if needed
  - start the Gateway in local/dev mode
  - open the browser Control UI
  - send a real prompt through the browser UI
  - prove the turn ran through `langgraph`, not `legacy`
  - prove the LangGraph sidecar/process started
  - run at least one loud negative case
  - prove the Gateway stayed responsive after the negative case

- Interface/parity:
  - `runEmbeddedPiAgent` signature preserved
  - `EmbeddedPiRunResult` shape preserved
- Routing:
  - default `legacy`
  - explicit `langgraph`
  - invalid mode handling
- State machine:
  - respond
  - clarification
  - execute
  - one-retry bound
- Failure handling:
  - sidecar unavailable before turn
  - sidecar crash mid-turn
  - RPC timeout
  - unknown execution intent
- Mapping:
  - exhaustive input mapping
  - exhaustive output mapping
  - non-serializable field rejection
- Approval:
  - approval resume with checkpoint
  - restart without checkpoint fails explicitly

Use the test names from `feature-docs/langgraph-turn-orchestrator/deliverables/03-failure-register.md` and `04-implementation-spec.md` as the minimum coverage set.

## Output Requirements

Deliver:
1. The implementation changes
2. Any spec updates required by changed discovery
3. A final report with:
   - changed files
   - whether `00a-success-criteria.md` passed completely
   - which requirements were satisfied
   - which failure-mode tests were added/run
   - any remaining `Gap` items
   - exact repo-relative file references for important code paths

If you must leave something unresolved, mark it as `Gap` and explain why it could not be completed from the current codebase.

## Hard Rules
1. Do NOT change the `runEmbeddedPiAgent` signature.
2. Do NOT change the `EmbeddedPiRunResult` type.
3. Do NOT change `runGatewayLoop`.
4. Do NOT expose the sidecar on a public network endpoint.
5. Do NOT fall back to legacy mid-turn.
6. Do NOT invent success. Failures must be loud.
7. Do NOT allow more than 1 retry in `verify_result`.
8. Default orchestration mode is `legacy`.
9. Do NOT claim full parity with the current broad embedded tool surface unless you actually implemented it; the v1 spec is shell-only for execute.

## Evidence
- `feature-docs/langgraph-turn-orchestrator/deliverables/04-implementation-spec.md` - authoritative implementation plan
- `feature-docs/langgraph-turn-orchestrator/deliverables/03-failure-register.md` - failure-mode list and test names
- `feature-docs/langgraph-turn-orchestrator/deliverables/02-requirements.md` - must-have requirements
- `feature-docs/langgraph-turn-orchestrator/deliverables/00b-system-profile.md` - verified current interfaces and caller inventory
