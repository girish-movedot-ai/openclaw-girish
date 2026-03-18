# Success Criteria: LangGraph Turn-Orchestrator Replacement

## Priority

This is the highest-priority document for this feature.

Implementation is not considered successful unless these success criteria pass in a real end-to-end run. Everything else is secondary to this document.

Priority order:

1. Pass the success criteria in this document
2. Satisfy the failure-handling requirements in `03-failure-register.md`
3. Preserve required interfaces and mappings from `00b-system-profile.md` and `04-implementation-spec.md`
4. Satisfy the remaining must-have requirements in `02-requirements.md`

## Core Problem

The problem is not that OpenClaw lacks memory. The problem is that memory is not being transformed into a stable, structured operating mind.

Every turn must begin from a deterministic reconstruction of:

- who the assistant is
- who the user is
- what the assistant can do
- what permissions it has
- what the current task context is
- how it should decide
- when it should act versus ask
- how it should stay fast on medium-complexity tasks

The goal is for OpenClaw to behave like a trained elite executive assistant, not like a generic assistant that partially starts over on each turn.

## Goal

A Cursor cloud agent must be able to initialize OpenClaw locally, use the browser-based Control UI, send real prompts through OpenClaw, and prove that the LangGraph orchestration layer reconstructs a stable operating mind on every turn while remaining fast and consistent.

This validation does **not** require connecting any messaging service. The browser UI path is sufficient.

## Required End-to-End Proof

The feature passes only if a Cursor cloud agent can prove all of the following:

1. OpenClaw can be initialized locally enough to run the Gateway and open the browser Control UI.
2. The Gateway starts successfully in a local/dev configuration.
3. The Control UI loads in the browser from the local Gateway.
4. A real prompt can be sent through the browser UI chat surface.
5. With orchestration mode set to `langgraph`, observable evidence shows the LangGraph path was selected.
6. The LangGraph sidecar/process starts successfully.
7. The turn uses a reconstructed operating mind, not a raw transcript echo or placeholder response.
8. The final reply is user-facing, coherent, and does not leak raw internal metadata.
9. The assistant preserves stable identity, user model, permissions, and active task context across turns.
10. The assistant makes correct act-versus-ask decisions for clear, ambiguous, and risky tasks.
11. Medium-complexity tasks stay fast enough to feel usable.
12. At least one forced negative case produces the expected loud failure behavior.
13. The Gateway remains responsive after the negative case.

## Initialization Instructions for the Cursor Agent

These steps are part of the success criteria. The implementation agent should perform them unless the environment is already prepared.

### 1) Prepare the local environment

- Install dependencies if needed with `pnpm install`
- Build any required local assets if the browser UI is missing
- Use a local/dev Gateway run, not a messaging-channel setup

### 2) Start OpenClaw locally

Use a local Gateway process suitable for development:

```bash
openclaw gateway run --dev --allow-unconfigured --force
```

Notes based on current docs:

- `openclaw gateway run` starts the local Gateway process
- `--dev` creates a dev config/workspace if missing
- `--allow-unconfigured` allows startup even if `gateway.mode=local` is not set yet
- `--force` clears any old listener on the Gateway port

If the browser UI assets are missing, build them first:

```bash
pnpm ui:build
```

### 3) Open the browser Control UI

Open the local Control UI in the browser:

- `http://127.0.0.1:18789/`

Localhost browser connections are auto-approved according to the current docs.

### 4) Use browser UI only

- Do not configure WhatsApp, Telegram, Discord, Slack, or any other messaging service
- Use the browser chat surface exposed by the Control UI
- The browser UI is the required verification surface for this milestone

## Pass/Fail Checks

### SC-001 Gateway starts locally

Pass if:

- the Gateway process starts without crashing
- the local Control UI URL becomes reachable

Fail if:

- the Gateway cannot start
- the UI never becomes reachable

### SC-002 Browser UI can send a real prompt

Pass if:

- the agent can open the Control UI
- the chat surface accepts a real prompt
- the run begins from the browser UI

Fail if:

- the UI loads but chat cannot be used

### SC-003 LangGraph path is actually selected

Pass if:

- orchestration mode is set to `langgraph`
- logs, events, traces, or other observable output show the LangGraph path was selected for the turn

Fail if:

- the turn silently uses the legacy path
- the agent cannot prove which path ran

### SC-004 Sidecar starts and is used

Pass if:

- the LangGraph sidecar/process starts during the run
- the agent can observe evidence of sidecar startup or use

Fail if:

- no sidecar starts
- the sidecar is expected but there is no proof it ran

### SC-005 Reconstructed operating mind is visible in behavior

Pass if:

- the turn behavior clearly reflects reconstructed identity, user model, permissions, and task posture
- the reply is not a raw metadata echo, placeholder text, or thin wrapper over the input
- graph-node logs or traces show the real decision pipeline ran

Fail if:

- the turn only proves routing
- the assistant behaves like a generic fresh assistant
- the reply looks like a debug dump or stub

### SC-006 Identity and preference continuity work across turns

Pass if:

- the assistant keeps a stable operating posture across multiple turns
- user preferences given once are followed later without reminders
- this still works after longer interaction or compaction-like pressure

Fail if:

- the assistant partially starts over after transcript growth
- the user has to repeat the same operating instructions

### SC-007 Tool and permission awareness is correct

Pass if:

- the assistant knows what tools it has
- the assistant knows when approval is needed
- the assistant uses tools when appropriate instead of ignoring them or pretending

Fail if:

- the assistant forgets tool availability
- the assistant hallucinates tool capability
- the assistant ignores permission boundaries

### SC-008 Action-versus-clarification decisions are correct

Pass if:

- a clear low-risk task leads to action
- an ambiguous task leads to clarification
- a risky task respects approval policy

Fail if:

- the assistant asks unnecessary questions
- the assistant acts when it should clarify
- the decision policy is inconsistent turn to turn

### SC-009 Medium-complexity tasks stay fast

Pass if:

- simple chat turns still feel normal
- medium-complexity tasks do not become obviously bloated or sluggish
- reconstruction adds discipline without making every turn heavy

Fail if:

- the system becomes over-deliberative
- medium tasks feel materially slower without clear benefit

### SC-010 Final reply is packaged correctly

Pass if:

- the final reply is user-facing and coherent
- internal sender/debug metadata does not leak into normal output
- execution results are verified before being presented as success

Fail if:

- replies contain raw internal objects, sender metadata, or trace dumps
- the system narrates success without verifying it

### SC-011 Negative case fails loudly

Pass if:

- at least one deliberate failure case is exercised
- the system returns an explicit failure
- the failure is observable in logs, events, or UI output
- the system does not report fake success

Suggested negative cases:

- sidecar unavailable before turn
- sidecar crash mid-turn
- RPC timeout

Fail if:

- the system silently falls back when it should not
- the system reports success for a failed run
- the Gateway becomes wedged or unresponsive

### SC-012 Gateway remains usable after failure

Pass if:

- after the negative test, the Gateway is still reachable
- the browser UI can still reconnect or continue operating

Fail if:

- the negative case leaves the Gateway unusable

## Required Acceptance Scenarios

The implementation is not done unless it passes scenarios like these:

1. Identity persistence: tell the assistant how to behave, continue for several turns, and confirm it still behaves that way later.
2. Preference persistence: state a working preference once, then confirm it is followed later without restating it.
3. Tool awareness: give a task that clearly requires a tool and confirm the assistant knows that it must use that tool.
4. Action-versus-ask: give one clear task and one ambiguous task and confirm the policy choice is correct in both.
5. Multi-turn task continuity: start a task, continue it over several turns, and confirm the assistant knows what is done and what remains.
6. Compaction resilience: continue a long-enough conversation and confirm the assistant still behaves like the same assistant with the same user and task posture.
7. Speed: run a medium-complexity task and confirm the system stays structured without becoming needlessly slow.

## Minimum Observable Evidence

The implementation agent must capture enough evidence to prove the result. At minimum:

- the command used to start the Gateway
- proof that the Control UI loaded
- proof that a prompt was sent through the browser UI
- proof that orchestration mode was `langgraph`
- proof that the LangGraph sidecar/process was started or invoked
- proof that the graph-node pipeline ran
- proof that the final reply was user-facing and not a raw metadata dump
- proof that continuity held across multiple turns
- proof that the Gateway stayed responsive after the test

Acceptable evidence sources:

- Gateway logs
- browser screenshots
- browser automation output
- RPC status/health checks
- structured events or trace fields

## Implementation Guidance

If the implementation must choose between:

- passing these end-to-end checks, or
- building a minimal routing-only placeholder,

do **not** choose the placeholder.

If the implementation must choose between:

- shipping a stub that proves LangGraph wiring, or
- shipping a real reconstruction-driven orchestrator,

choose the real orchestrator.

The feature is not successful if it only proves that the sidecar starts and returns some reply. It is successful only if each turn reconstructs a stable operating mind and uses it to drive fast, consistent, tool-aware behavior.

## Evidence

- `docs/cli/gateway.md` - local Gateway startup flags, `--dev`, `--allow-unconfigured`, `--force`
- `docs/web/control-ui.md` - local Control UI URL, browser UI behavior, localhost approval behavior, UI build command
