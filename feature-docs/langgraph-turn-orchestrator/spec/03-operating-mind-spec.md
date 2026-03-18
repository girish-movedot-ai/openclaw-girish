# Operating Mind Spec

This document defines the behavior contract for the real LangGraph implementation.

It exists because routing, sidecar startup, and valid replies are not enough. The goal is a stable operating mind that is reconstructed on every turn.

## Purpose

Every LangGraph turn must begin by rebuilding a structured working mind from durable state, not from fragile transcript recall alone.

That operating mind must make the assistant behave like a trained executive assistant:

- stable identity
- stable user model
- correct tool awareness
- correct permission awareness
- correct action-versus-clarification decisions
- fast enough behavior on medium-complexity tasks

## Highest-Level Rule

The graph is not successful if it only proves:

- LangGraph routing worked
- the sidecar started
- a reply was returned

The graph is successful only if it reconstructs a stable operating mind and uses it to drive correct turn behavior.

## Operating Mind State

The LangGraph state must contain explicit sections for the following.

### 1) Assistant Identity

This section answers:

- who the assistant is
- what role it should play
- what tone and posture it should keep
- what standards it should hold itself to

Minimum contents:

- assistant name or identity label
- operating role
- response style guidance
- decision posture
- speed-vs-thoroughness posture

### 2) User Model

This section answers:

- who the user is
- what preferences matter
- what working style the user expects
- what standing instructions should be followed without being repeated

Minimum contents:

- stable user preferences
- formatting preferences
- interaction preferences
- known tolerance for autonomy vs confirmation
- known quality expectations

### 3) Capability Model

This section answers:

- what tools exist
- what channels or surfaces exist
- what the host can actually execute
- what is unavailable right now

Minimum contents:

- available tools
- unavailable tools
- browser/UI surfaces
- execution surfaces
- relevant runtime limits

### 4) Permission Model

This section answers:

- what can be done immediately
- what requires approval
- what must never be done automatically

Minimum contents:

- approval requirements
- dangerous actions
- forbidden actions
- current policy overrides

### 5) Task Context

This section answers:

- what task is active
- what has already been done
- what remains
- what the current turn is trying to achieve

Minimum contents:

- active objective
- subtask list
- current status
- blockers
- recent task-relevant outputs

### 6) Decision Policy

This section answers:

- when to act
- when to ask
- when to escalate
- when to execute

Minimum contents:

- low-risk action rules
- ambiguity rules
- approval rules
- escalation triggers

### 7) Performance Posture

This section answers:

- how much planning is allowed
- how much latency is acceptable
- when to keep things lightweight

Minimum contents:

- bounded planning budget
- retry budget
- medium-task latency target
- lightweight default posture

## Seven-Phase Orchestration Contract

The LangGraph implementation should follow these phases.

### Phase 1: Reconstruct State

Inputs:

- persisted identity state
- persisted user model
- persisted permissions and capabilities
- persisted task state
- relevant current-session context

Outputs:

- structured operating mind object
- missing-information list
- confidence notes for uncertain fields

Hard rules:

- do not rely only on the raw transcript
- prefer structured state over loose memory fragments
- mark unknown fields explicitly

### Phase 2: Parse Task

Inputs:

- user message
- operating mind
- current task context

Outputs:

- normalized task interpretation
- task type
- ambiguity flags
- risk level

Hard rules:

- separate user intent from raw wording
- identify whether the task is simple, medium, or high-risk

### Phase 3: Route by Policy

Inputs:

- parsed task
- permission model
- capability model
- decision policy

Outputs:

- one of: `respond`, `ask_clarification`, `execute`, `escalate`
- justification record

Hard rules:

- clear low-risk tasks should not trigger unnecessary clarification
- ambiguous tasks should not trigger confident execution
- risky tasks must respect approval boundaries

### Phase 4: Bounded Planning

Inputs:

- chosen intent
- parsed task
- operating mind

Outputs:

- short execution plan
- no-op plan for direct response
- retry count baseline

Hard rules:

- planning must be bounded
- medium tasks should stay lightweight
- avoid long reflective loops

### Phase 5: Execute

Inputs:

- execution plan
- host execution surfaces
- approval rules

Outputs:

- execution result
- approval status
- execution evidence

Hard rules:

- all real execution stays in the TS host
- the graph never fabricates execution

### Phase 6: Verify and Package

Inputs:

- execution result or direct-response result
- operating mind
- active task context

Outputs:

- final user-facing reply
- completion status
- structured failure when needed

Hard rules:

- normal replies must not leak internal metadata
- do not claim success without verification
- packaging must be clean and user-facing

### Phase 7: Write Back State

Inputs:

- final turn result
- newly learned preferences
- updated task context

Outputs:

- updated structured state for the next turn

Hard rules:

- write back only durable, useful state
- task progress and stable preferences must survive future turns

## Decision Rules

### Act

Choose `act` or `execute` when:

- the task is clear
- the needed tools are available
- the action is allowed without clarification

### Ask Clarification

Choose `ask_clarification` when:

- the request is materially ambiguous
- success depends on information the system does not have
- multiple materially different actions would be reasonable

### Escalate

Choose `escalate` when:

- the task exceeds permissions
- the task exceeds supported capability
- policy says the assistant should not proceed

## Write-Back Rules

The graph should write back:

- stable user preferences
- stable operating instructions
- durable task progress
- useful derived context for future turns

The graph should not write back:

- raw transient debug state
- one-off noise
- low-confidence guesses presented as facts

## Latency Targets

These are qualitative targets for v1.

- simple turns should feel near-normal
- medium-complexity tasks should remain comfortably usable
- reconstruction should add discipline, not obvious drag
- bounded planning should stay short

If there is a tradeoff, prefer:

- real reconstruction with bounded cost

over:

- fake fast behavior that loses continuity

But prefer:

- lightweight structured reasoning

over:

- heavy over-deliberation on every turn

## Acceptance Tests

### Test 1: Identity Persistence

- tell the assistant who it is
- continue for several turns
- confirm it keeps the same posture later

### Test 2: Preference Persistence

- give a preference once
- ask for related work later
- confirm it is followed without reminders

### Test 3: Tool Awareness

- give a task that clearly requires a tool
- confirm the assistant recognizes the tool and uses it

### Test 4: Permission Awareness

- give a task that requires approval
- confirm the assistant respects the approval boundary

### Test 5: Action vs Ask

- give one clear request and one ambiguous request
- confirm the policy choice is correct in each case

### Test 6: Multi-Turn Task Continuity

- start a task
- continue over several turns
- confirm the assistant knows what is done and what remains

### Test 7: Compaction Resilience

- continue long enough to create context pressure
- confirm the assistant still behaves like the same assistant with the same user and task posture

### Test 8: Packaging Quality

- send a simple message like `Hello`
- confirm the response is a proper assistant reply, not raw metadata or a debug dump

## Non-Acceptable Outcomes

These outcomes fail this spec even if LangGraph technically runs:

- raw sender metadata shown in normal replies
- transcript echo presented as reasoning
- generic assistant behavior after prior instructions were given
- forgetting tools or permissions it should know
- acting unsure about active task context when that context should have been reconstructed
- noticeably bloated reasoning for medium-complexity work

## Relationship to Other Docs

- `01-design-intent.md` says what the architecture is trying to achieve
- this document defines what the real reconstructed operating mind must do
- `deliverables/00a-success-criteria.md` is still the highest-priority acceptance document
