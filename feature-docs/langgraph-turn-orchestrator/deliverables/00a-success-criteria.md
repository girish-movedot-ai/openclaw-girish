# Success Criteria: LangGraph Turn-Orchestrator Replacement

## Priority

This is the highest-priority deliverable for this feature.

Implementation is not considered successful unless these success criteria pass in a real end-to-end run.

Priority order:
1. Pass the success criteria in this document
2. Satisfy the failure-handling requirements in `03-failure-register.md`
3. Preserve required interfaces and mappings from `00b-system-profile.md` and `04-implementation-spec.md`
4. Satisfy the remaining must-have requirements in `02-requirements.md`

## Goal

A Cursor cloud agent must be able to initialize OpenClaw locally, use the browser-based Control UI, send a real prompt through OpenClaw, and verify that the LangGraph orchestrator path ran instead of the legacy path.

This validation does **not** require connecting any messaging service. The browser UI path is sufficient.

## Required End-to-End Proof

The feature passes only if a Cursor cloud agent can prove all of the following:

1. OpenClaw can be initialized locally enough to run the Gateway and open the browser Control UI.
2. The Gateway starts successfully in a local/dev configuration.
3. The Control UI loads in the browser from the local Gateway.
4. A real prompt can be sent through the browser UI chat surface.
5. With orchestration mode set to `langgraph`, observable evidence shows the LangGraph path was selected.
6. The LangGraph sidecar/process starts successfully.
7. The turn completes with a valid final reply.
8. Observable evidence shows the turn used `langgraph`, not `legacy`.
9. At least one forced negative case produces the expected loud failure behavior.
10. The Gateway remains responsive after the negative case.

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

### SC-005 One real turn completes end to end

Pass if:
- a real prompt sent from the Control UI completes
- the final reply is returned to the UI
- the Gateway stays healthy after the turn

Fail if:
- the run hangs
- the run crashes
- the UI never receives a final reply

### SC-006 Negative case fails loudly

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

### SC-007 Gateway remains usable after failure

Pass if:
- after the negative test, the Gateway is still reachable
- the browser UI can still reconnect or continue operating

Fail if:
- the negative case leaves the Gateway unusable

## Minimum Observable Evidence

The implementation agent must capture enough evidence to prove the result. At minimum:

- the command used to start the Gateway
- proof that the Control UI loaded
- proof that a prompt was sent through the browser UI
- proof that orchestration mode was `langgraph`
- proof that the LangGraph sidecar/process was started or invoked
- proof of the final reply or explicit failure
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
- adding extra architectural polish,

choose the end-to-end checks first.

If the implementation must choose between:
- passing these success criteria, or
- adding lower-priority nice-to-have behavior,

choose the success criteria first.

## Evidence

- `docs/cli/gateway.md` - local Gateway startup flags, `--dev`, `--allow-unconfigured`, `--force`
- `docs/web/control-ui.md` - local Control UI URL, browser UI behavior, localhost approval behavior, UI build command
