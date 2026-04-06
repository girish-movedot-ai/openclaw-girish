#!/usr/bin/env python3
"""
LangGraph turn orchestrator sidecar.

Implements the reconstruction-driven operating mind as defined in:
  spec/03-operating-mind-spec.md
  deliverables/04-implementation-spec.md

Seven-phase graph contract:
  Phase 1: ingest_turn -> reconstruct_state   (operating mind reconstruction)
  Phase 2: diagnose_unknowns                  (parse task, identify gaps)
  Phase 3: decide_intent                      (route: respond/execute/ask/escalate)
  Phase 4: build_execution_request            (bounded planning for execute path)
  Phase 5: await_host_execution [interrupt]   (host executes via existing surfaces)
  Phase 6: verify_result -> render_reply      (verify + package user-facing reply)
  Phase 7: persist_turn_artifacts             (write back durable state)

RPC: stdio JSON lines (id/method/params in, id/status/result out).
LLM: Uses Anthropic Claude via ANTHROPIC_API_KEY env var.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Any, Optional, TypedDict

# LangGraph imports
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

# ---------------------------------------------------------------------------
# State schema (TypedDict — required for per-field channel semantics in LangGraph)
# ---------------------------------------------------------------------------


class TurnState(TypedDict, total=False):
    """Full LangGraph turn state. Each field is a separate channel."""

    # Inputs
    turn: Optional[dict[str, Any]]

    # Reconstruction (Phase 1)
    operating_mind: Optional[dict[str, Any]]
    session_history: Optional[list[dict[str, str]]]
    mem0_memories: Optional[list[str]]

    # Diagnosis (Phase 2)
    unknowns: Optional[list[str]]
    blocking_reason: Optional[str]

    # Decision (Phase 3)
    intent: Optional[str]
    pending_reply: Optional[str]
    execution_request: Optional[dict[str, Any]]
    justification: Optional[str]

    # Execution (Phase 5)
    execution_result: Optional[dict[str, Any]]

    # Retry tracking
    retry_count: int

    # Verification (Phase 6 pre)
    verification_passed: bool
    blocked: bool
    should_retry: bool

    # Reply (Phase 6)
    final_response: Optional[dict[str, Any]]

    # Persistence (Phase 7)
    state_written: bool

    # Error
    error: Optional[dict[str, str]]


# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

_CHECKPOINTER: MemorySaver = MemorySaver()
_GRAPH: Any = None  # lazily initialized
_MEM0_CLIENT: Any = None
_MEM0_DISABLED_REASON: Optional[str] = None


# ---------------------------------------------------------------------------
# I/O utilities
# ---------------------------------------------------------------------------


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _log(message: str) -> None:
    sys.stderr.write(f"[langgraph] {message}\n")
    sys.stderr.flush()


def _structured_error(kind: str, message: str) -> dict[str, str]:
    return {"kind": kind, "message": message}


def _resolve_mem0_user_id(turn: dict[str, Any]) -> str:
    for candidate in (
        turn.get("senderId"),
        turn.get("agentAccountId"),
        turn.get("senderUsername"),
        turn.get("senderE164"),
        turn.get("sessionId"),
    ):
        if candidate is None:
            continue
        value = str(candidate).strip()
        if value:
            return value
    return "anonymous"


def _get_mem0_client() -> Any:
    global _MEM0_CLIENT, _MEM0_DISABLED_REASON
    if _MEM0_CLIENT is not None:
        return _MEM0_CLIENT
    api_key = os.environ.get("MEM0_API_KEY", "").strip()
    if not api_key:
        if _MEM0_DISABLED_REASON != "missing_api_key":
            _MEM0_DISABLED_REASON = "missing_api_key"
            _log("mem0 disabled: MEM0_API_KEY is not set")
        return None
    try:
        from mem0 import MemoryClient  # noqa: PLC0415

        _MEM0_CLIENT = MemoryClient(api_key=api_key)
        _MEM0_DISABLED_REASON = None
        _log("mem0 enabled: MemoryClient initialized")
        return _MEM0_CLIENT
    except Exception as exc:
        _MEM0_DISABLED_REASON = "client_init_failed"
        _log(f"mem0 disabled: client init failed: {exc}")
        return None


def _search_mem0_memories(turn: dict[str, Any]) -> list[str]:
    client = _get_mem0_client()
    if client is None:
        return []
    prompt = str(turn.get("prompt") or "").strip()
    if not prompt:
        return []
    try:
        response = client.search(
            prompt,
            filters={"user_id": _resolve_mem0_user_id(turn)},
        )
        results = response.get("results") if isinstance(response, dict) else None
        if not isinstance(results, list):
            return []
        memories: list[str] = []
        for item in results[:5]:
            if not isinstance(item, dict):
                continue
            memory = str(item.get("memory") or "").strip()
            if memory:
                memories.append(memory[:300])
        if memories:
            _log(f"mem0 search: retrieved {len(memories)} memories")
        return memories
    except Exception as exc:
        _log(f"mem0 search error: {exc}")
        return []


def _store_turn_in_mem0(turn: dict[str, Any], response: dict[str, Any]) -> None:
    client = _get_mem0_client()
    if client is None:
        return
    prompt = str(turn.get("prompt") or "").strip()
    payloads = response.get("payloads") or []
    reply_text = ""
    if isinstance(payloads, list):
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            text = str(payload.get("text") or "").strip()
            if text:
                reply_text = text
                break
    terminal_state = str(response.get("terminalState") or "")
    if not prompt or not reply_text or terminal_state == "blocked_waiting_for_user":
        return
    try:
        client.add(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": reply_text},
            ],
            user_id=_resolve_mem0_user_id(turn),
        )
        _log("mem0 add: stored turn memory")
    except Exception as exc:
        _log(f"mem0 add error: {exc}")


# ---------------------------------------------------------------------------
# Session history reading
# ---------------------------------------------------------------------------


def _read_session_history(session_file: str, max_entries: int = 40) -> list[dict[str, Any]]:
    """Read recent entries from the pi-agent session JSONL file."""
    if not session_file or not os.path.exists(session_file):
        return []
    try:
        entries: list[dict[str, Any]] = []
        with open(session_file, encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                    if isinstance(obj, dict):
                        entries.append(obj)
                except json.JSONDecodeError:
                    continue
        return entries[-max_entries:]
    except Exception as exc:
        _log(f"session read error: {exc}")
        return []


def _extract_message_pairs(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Extract user/assistant message pairs from session entries for LLM context."""
    messages: list[dict[str, str]] = []

    def _text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict):
                    parts.append(str(c.get("text") or c.get("content") or ""))
                elif isinstance(c, str):
                    parts.append(c)
            return " ".join(parts)
        return str(content or "")

    for entry in entries:
        role = str(entry.get("type") or entry.get("role") or "")

        # Direct message entries
        if role in ("human", "user"):
            text = _text(entry.get("content") or entry.get("text") or "")
            if text.strip():
                messages.append({"role": "user", "content": text[:800]})
        elif role in ("ai", "assistant"):
            text = _text(entry.get("content") or entry.get("text") or "")
            if text.strip():
                messages.append({"role": "assistant", "content": text[:800]})

        # Entries with a nested messages array (pi-agent session format)
        elif "messages" in entry and isinstance(entry["messages"], list):
            for msg in entry["messages"]:
                mrole = str(msg.get("role") or msg.get("type") or "")
                mtext = _text(msg.get("content") or msg.get("text") or "")
                if not mtext.strip():
                    continue
                if mrole in ("user", "human"):
                    messages.append({"role": "user", "content": mtext[:800]})
                elif mrole in ("assistant", "ai"):
                    messages.append({"role": "assistant", "content": mtext[:800]})

    # Return the last 14 messages (7 turn-pairs) to keep context bounded
    return messages[-14:]


# ---------------------------------------------------------------------------
# Operating mind reconstruction  (Phase 1)
# ---------------------------------------------------------------------------


def _reconstruct_operating_mind(
    turn: dict[str, Any], history_entries: list[dict[str, Any]], mem0_memories: list[str]
) -> dict[str, Any]:
    """
    Build a structured operating mind from session state and turn context.

    All seven sections from spec/03-operating-mind-spec.md are populated:
    1. assistant_identity  2. user_model  3. capability_model
    4. permission_model    5. task_context 6. decision_policy  7. performance_posture
    """
    config = turn.get("config") or {}
    extra_system = str(turn.get("extraSystemPrompt") or "")
    agent_id = str(turn.get("agentId") or "main")
    sender_name = str(turn.get("senderName") or "User")
    sender_id = turn.get("senderId")
    is_owner = bool(turn.get("senderIsOwner"))
    disable_tools = bool(turn.get("disableTools"))
    channel = str(turn.get("messageChannel") or turn.get("messageProvider") or "web")

    exec_overrides = turn.get("execOverrides") or {}
    host_surface = "gateway"
    if isinstance(exec_overrides, dict) and exec_overrides.get("host"):
        host_surface = str(exec_overrides["host"])

    # Derive standing instructions and active task from history
    history_messages = _extract_message_pairs(history_entries)
    standing_instructions: list[str] = []
    active_objective: str | None = None

    for msg in history_messages:
        if msg["role"] != "user":
            continue
        text = msg["content"]
        lower = text.lower()
        if any(
            kw in lower
            for kw in [
                "always ", "never ", "please always", "please never",
                "from now on", "going forward", "remember that",
                "i prefer", "i want you to", "you should always",
                "you should never", "format your", "respond in",
                "don't use ", "please use ", "use only",
            ]
        ):
            if text not in standing_instructions:
                standing_instructions.append(text[:300])

    # Active task from most recent substantive user message (not current turn)
    for msg in reversed(history_messages[:-1]):
        if msg["role"] == "user" and len(msg["content"].strip()) > 20:
            active_objective = msg["content"].strip()[:200]
            break

    available_tools: list[str] = []
    if not disable_tools:
        available_tools = ["shell_command"]
        if host_surface and host_surface != "none":
            available_tools.append(f"exec:{host_surface}")

    return {
        "assistant_identity": {
            "name": "OpenClaw",
            "role": f"embedded AI agent (id: {agent_id})",
            "style": extra_system.strip() if extra_system.strip() else (
                "Helpful, precise, and action-oriented. "
                "Give concise, direct responses. "
                "Match the user's expected format and detail level."
            ),
            "decision_posture": (
                "Act directly on clear, low-risk requests without unnecessary questions. "
                "Ask for clarification only when intent is genuinely ambiguous. "
                "Escalate when the request exceeds available permissions or capability."
            ),
            "speed_posture": (
                "Lightweight by default. One bounded LLM call per turn. "
                "One retry maximum on execution failures."
            ),
        },
        "user_model": {
            "name": sender_name,
            "sender_id": sender_id,
            "is_owner": is_owner,
            "channel": channel,
            "standing_instructions": standing_instructions,
            "mem0_memories": mem0_memories,
            "interaction_style": "direct and technical" if is_owner else "standard",
            "quality_expectations": "accurate, complete, user-friendly responses",
        },
        "capability_model": {
            "available_tools": available_tools,
            "unavailable_tools": ["memory_write", "email_send", "http_call", "browser_control"],
            "channel": channel,
            "execution_surfaces": [f"host:{host_surface}"] if not disable_tools else [],
            "disable_tools": disable_tools,
            "workspace_dir": str(turn.get("workspaceDir") or ""),
            "fast_mode": bool(turn.get("fastMode")),
        },
        "permission_model": {
            "approval_required": [
                "destructive filesystem operations",
                "system configuration changes",
                "external network requests",
                "operations with irreversible side effects",
            ],
            "dangerous_actions": ["rm -rf", "format disk", "chmod 777 /", "overwrite /etc"],
            "forbidden_actions": [
                "expose credentials or API keys",
                "modify gateway configuration without approval",
                "access private key material",
            ],
            "current_policy": (
                "Ask before irreversible actions. "
                "Owner has elevated trust. "
                "Require approval for dangerous shell commands."
            ),
            "is_owner": is_owner,
        },
        "task_context": {
            "active_objective": active_objective,
            "subtasks": [],
            "status": "ready",
            "blockers": [],
            "recent_outputs": [],
            "memory_context": mem0_memories,
            "session_id": str(turn.get("sessionId") or ""),
            "run_id": str(turn.get("runId") or ""),
            "history_depth": len(history_messages),
        },
        "decision_policy": {
            "low_risk_act": (
                "Task is clear, tools are available, action is reversible "
                "or owner has approved category"
            ),
            "ask_clarification": (
                "Multiple valid interpretations exist, "
                "critical information is missing"
            ),
            "escalate": (
                "Request exceeds permissions, "
                "request exceeds supported capability"
            ),
            "execute_requires": (
                "Clear command intent, specific shell action, "
                "tool surface available, not disabled"
            ),
        },
        "performance_posture": {
            "planning_budget": "one LLM call per decision phase",
            "retry_budget": 1,
            "latency_target": "fast for simple turns, comfortably usable for medium",
            "default_posture": "lightweight — avoid over-deliberation",
        },
    }


# ---------------------------------------------------------------------------
# LLM integration
# ---------------------------------------------------------------------------


def _resolve_model() -> str:
    return os.environ.get("OPENCLAW_LANGGRAPH_MODEL", "claude-haiku-4-5").strip()


def _llm_call(
    system: str,
    messages: list[dict[str, str]],
    max_tokens: int = 1500,
) -> str:
    """Call Anthropic Claude and return the text response."""
    import anthropic  # noqa: PLC0415

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set; LLM inference unavailable.")

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=_resolve_model(),
        max_tokens=max_tokens,
        system=system,
        messages=messages,  # type: ignore[arg-type]
    )
    if response.content:
        return response.content[0].text  # type: ignore[union-attr]
    return ""


def _build_system_prompt(mind: dict[str, Any], turn: dict[str, Any]) -> str:
    """Build the orchestrator system prompt from the reconstructed operating mind."""
    identity = mind.get("assistant_identity") or {}
    user_model = mind.get("user_model") or {}
    capability = mind.get("capability_model") or {}
    task_ctx = mind.get("task_context") or {}
    permission = mind.get("permission_model") or {}

    instructions = user_model.get("standing_instructions") or []
    instructions_text = (
        "\n".join(f"  - {inst}" for inst in instructions[:5])
        if instructions
        else "  (none recorded yet)"
    )
    memories = user_model.get("mem0_memories") or task_ctx.get("memory_context") or []
    memories_text = (
        "\n".join(f"  - {memory}" for memory in memories[:5])
        if memories
        else "  (none retrieved)"
    )
    tools_text = ", ".join(capability.get("available_tools") or ["none"])
    active_task = task_ctx.get("active_objective") or "none"
    history_depth = int(task_ctx.get("history_depth") or 0)

    return f"""You are {identity.get("name", "OpenClaw")}.
Role: {identity.get("role", "AI assistant")}
Style: {identity.get("style", "helpful, precise")}
Decision posture: {identity.get("decision_posture", "act on clear tasks, ask when ambiguous")}

User: {user_model.get("name", "User")} | Channel: {user_model.get("channel", "web")} | Owner: {user_model.get("is_owner", False)}
Standing instructions from this user:
{instructions_text}

Relevant long-term memories:
{memories_text}

Available tools: {tools_text}
Tools disabled: {capability.get("disable_tools", False)}
Workspace: {capability.get("workspace_dir", "")}

Permissions policy: {permission.get("current_policy", "standard")}
Requires approval: {", ".join(permission.get("approval_required") or [])}

Active task context: {active_task}
Conversation history depth: {history_depth} messages

Core rules:
- Respond as a helpful AI assistant — never expose internal metadata, sender IDs, or debug info
- Follow standing instructions without requiring the user to repeat them
- Act on clear, low-risk tasks without unnecessary questions
- Ask for clarification only when intent is genuinely ambiguous
- Keep responses user-friendly, direct, and appropriately concise"""


def _decide_with_llm(
    turn: dict[str, Any],
    mind: dict[str, Any],
    history_messages: list[dict[str, str]],
) -> dict[str, Any]:
    """Use Claude to decide intent and generate the reply."""
    prompt_text = str(turn.get("prompt") or "").strip()
    system = _build_system_prompt(mind, turn)

    messages: list[dict[str, str]] = []
    for msg in history_messages[-8:]:
        messages.append({"role": msg["role"], "content": msg["content"]})

    decision_prompt = f"""{prompt_text}

---
Analyze the above message and respond with a JSON object in exactly this format:
{{
  "intent": "respond" | "execute" | "ask_clarification" | "escalate",
  "reply": "<user-facing response text>",
  "command": "<shell command if intent is execute, otherwise null>",
  "requires_approval": <true or false, only relevant when intent is execute>,
  "justification": "<one sentence explaining the intent choice>"
}}

Intent selection rules:
- "respond": conversational messages, questions, tasks that only need a text reply
- "execute": user explicitly wants a command run, file operation, or system action
- "ask_clarification": genuinely ambiguous with multiple valid interpretations or missing info
- "escalate": exceeds permissions or system capability

The "reply" field must ALWAYS be a natural, helpful assistant response — not JSON, not metadata.
If intent is "execute", make reply describe what you are about to do.
Set requires_approval=true if the command is destructive, touches system files, or modifies critical state."""

    messages.append({"role": "user", "content": decision_prompt})

    response_text = _llm_call(system, messages, max_tokens=1200)

    try:
        json_text = response_text.strip()
        if "```json" in json_text:
            json_text = json_text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in json_text:
            json_text = json_text.split("```", 1)[1].split("```", 1)[0].strip()

        parsed = json.loads(json_text)
        intent = str(parsed.get("intent") or "respond").strip()
        if intent not in ("respond", "execute", "ask_clarification", "escalate"):
            intent = "respond"

        reply = str(parsed.get("reply") or "").strip()
        if not reply:
            reply = "I'm here to help. What would you like to work on?"

        command = parsed.get("command")
        requires_approval = bool(parsed.get("requires_approval"))
        justification = str(parsed.get("justification") or "").strip()

        if intent == "execute" and (not command or not str(command).strip()):
            intent = "respond"

        return {
            "intent": intent,
            "reply": reply,
            "command": str(command).strip() if command else None,
            "requires_approval": requires_approval,
            "justification": justification,
        }
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        _log(f"LLM JSON parse error: {exc}; using raw text as reply")
        clean = response_text.strip()
        if not clean or len(clean) > 3000:
            clean = "I'm ready to help. What would you like to work on?"
        return {
            "intent": "respond",
            "reply": clean,
            "command": None,
            "requires_approval": False,
            "justification": f"JSON parse error fallback: {str(exc)[:80]}",
        }


def _generate_execution_reply(
    turn: dict[str, Any],
    mind: dict[str, Any],
    exec_request: dict[str, Any],
    exec_result: dict[str, Any],
) -> str:
    """Generate a user-facing reply after host execution completes."""
    status = str(exec_result.get("status") or "unknown")
    payload = exec_result.get("payload") or {}
    output = str(payload.get("output") or "").strip()
    exit_code = payload.get("exitCode")
    command = str(exec_request.get("command") or "")

    if status == "completed":
        system = (
            f"You are {(mind.get('assistant_identity') or {}).get('name', 'OpenClaw')}. "
            "A shell command just completed. Report the result to the user clearly and helpfully. "
            "Be concise. Include relevant output. Do not expose raw technical metadata "
            "unless it is meaningful to the user."
        )
        output_section = f"\nOutput:\n{output[:2000]}" if output else "\n(no output)"
        prompt_msg = (
            f"Command ran: `{command}`\n"
            f"Exit code: {exit_code}"
            f"{output_section}\n\n"
            "Summarize this result for the user in 2-3 sentences."
        )
        try:
            reply = _llm_call(system, [{"role": "user", "content": prompt_msg}], max_tokens=400)
            return reply.strip()
        except Exception:
            suffix = f"\n{output[:500]}" if output else ""
            return f"Command completed successfully.{suffix}"

    if status == "cancelled":
        return "The command was cancelled. No changes were made."

    detail = output or (f"exit code {exit_code}" if exit_code is not None else "unknown error")
    return f"The command did not complete successfully: {detail}"


# ---------------------------------------------------------------------------
# LangGraph nodes
# ---------------------------------------------------------------------------


def _node_ingest_turn(state: TurnState) -> TurnState:
    """Initialize turn state (Phase 1 start)."""
    turn = state.get("turn") or {}
    _log(f"node=ingest_turn runId={turn.get('runId', '?')}")
    return {
        "retry_count": 0,
        "error": None,
        "execution_request": None,
        "execution_result": None,
        "final_response": None,
        "mem0_memories": [],
        "pending_reply": None,
        "session_history": [],
        "operating_mind": None,
        "unknowns": [],
        "blocking_reason": None,
        "intent": None,
        "justification": "",
        "verification_passed": False,
        "blocked": False,
        "should_retry": False,
        "state_written": False,
    }


def _node_reconstruct_state(state: TurnState) -> TurnState:
    """Phase 1: Reconstruct operating mind from persisted session state."""
    turn = state.get("turn") or {}
    _log(f"node=reconstruct_state sessionId={turn.get('sessionId', '?')}")

    session_file = str(turn.get("sessionFile") or "")
    history_entries = _read_session_history(session_file, max_entries=40)
    history_messages = _extract_message_pairs(history_entries)
    mem0_memories = _search_mem0_memories(turn)
    mind = _reconstruct_operating_mind(turn, history_entries, mem0_memories)

    n_instr = len((mind.get("user_model") or {}).get("standing_instructions") or [])
    n_tools = len((mind.get("capability_model") or {}).get("available_tools") or [])
    active = bool((mind.get("task_context") or {}).get("active_objective"))
    _log(
        f"operating_mind reconstructed: "
        f"standing_instructions={n_instr} "
        f"mem0_memories={len(mem0_memories)} "
        f"available_tools={n_tools} "
        f"active_task={active} "
        f"history_messages={len(history_messages)}"
    )

    return {
        "mem0_memories": mem0_memories,
        "operating_mind": mind,
        "session_history": history_messages,
    }


def _node_diagnose_unknowns(state: TurnState) -> TurnState:
    """Phase 2: Parse task and identify blocking unknowns."""
    turn = state.get("turn") or {}
    prompt = str(turn.get("prompt") or "").strip()
    retry_count = int(state.get("retry_count") or 0)
    _log(f"node=diagnose_unknowns prompt_len={len(prompt)} retry={retry_count}")

    unknowns: list[str] = []
    blocking_reason: str | None = None

    if not prompt:
        unknowns.append("missing_user_message")
        blocking_reason = "No user message provided."

    return {
        "unknowns": unknowns,
        "blocking_reason": blocking_reason,
    }


def _node_decide_intent(state: TurnState) -> TurnState:
    """Phase 3: Route by policy using LLM reasoning."""
    turn = state.get("turn") or {}
    mind = state.get("operating_mind") or {}
    history_messages = state.get("session_history") or []
    unknowns = state.get("unknowns") or []
    retry_count = int(state.get("retry_count") or 0)
    _log(f"node=decide_intent unknowns={unknowns} retry={retry_count}")

    # Test mode hooks (preserved for automated failure testing)
    test_mode = os.environ.get("OPENCLAW_LANGGRAPH_TEST_MODE", "").strip().lower()
    if test_mode == "stall_invoke":
        _log("test_mode=stall_invoke: sleeping 35s to trigger timeout")
        time.sleep(35)
    if test_mode == "crash_invoke":
        _log("test_mode=crash_invoke: forcing sidecar exit")
        os._exit(17)

    if "missing_user_message" in unknowns:
        return {
            "intent": "ask_clarification",
            "pending_reply": "Please share what you'd like help with.",
            "execution_request": None,
            "justification": "no user message present",
        }

    try:
        result = _decide_with_llm(turn, mind, history_messages)
    except Exception as exc:
        _log(f"decide_intent LLM error: {exc}")
        prompt_text = str(turn.get("prompt") or "").strip()
        result = {
            "intent": "respond",
            "reply": (
                f"I received your message and I'm ready to help. {prompt_text[:200]}"
                if prompt_text
                else "I'm ready to help."
            ),
            "command": None,
            "requires_approval": False,
            "justification": f"LLM unavailable: {str(exc)[:100]}",
        }

    intent = result["intent"]
    pending_reply = result["reply"]
    command = result.get("command")
    requires_approval = bool(result.get("requires_approval"))

    _log(
        f"decide_intent: intent={intent} "
        f"justification={result.get('justification', '')[:80]}"
    )

    exec_request: dict[str, Any] | None = None
    if intent == "execute" and command:
        exec_request = {
            "idempotencyKey": str(turn.get("runId") or uuid.uuid4()),
            "intent": "shell",
            "command": command,
            "cwd": turn.get("workspaceDir"),
            "requiresApproval": requires_approval,
            "verificationContract": {"expectExitCode": 0},
        }

    return {
        "intent": intent,
        "pending_reply": pending_reply,
        "execution_request": exec_request,
        "justification": result.get("justification") or "",
    }


def _node_build_execution_request(state: TurnState) -> TurnState:
    """Phase 4: Bounded planning — execution request already built in decide_intent."""
    exec_req = state.get("execution_request") or {}
    _log(
        f"node=build_execution_request "
        f"intent={exec_req.get('intent')} "
        f"command={str(exec_req.get('command', ''))[:60]} "
        f"approval={exec_req.get('requiresApproval')}"
    )
    return {}  # type: ignore[return-value]


def _node_await_host_execution(state: TurnState) -> TurnState:
    """Phase 5: Interrupt for host execution via existing OpenClaw surfaces."""
    exec_request = state.get("execution_request") or {}
    _log(
        f"node=await_host_execution: pausing for host execution "
        f"intent={exec_request.get('intent')} "
        f"command={str(exec_request.get('command', ''))[:60]}"
    )
    # Pause the graph; TS host receives exec_request, runs it, and resumes
    execution_result: dict[str, Any] = interrupt(exec_request)
    _log(f"node=await_host_execution: resumed status={execution_result.get('status')}")
    return {"execution_result": execution_result}


def _node_verify_result(state: TurnState) -> TurnState:
    """Phase 6 (pre): Verify execution result and decide next step."""
    result = state.get("execution_result") or {}
    retry_count = int(state.get("retry_count") or 0)
    status = str(result.get("status") or "unknown")
    _log(f"node=verify_result status={status} retry_count={retry_count}")

    if status == "completed":
        return {"verification_passed": True, "blocked": False, "should_retry": False}

    if status in ("cancelled", "approval_pending"):
        return {"verification_passed": False, "blocked": True, "should_retry": False}

    # Failed — allow exactly one retry
    if retry_count < 1:
        _log("verify_result: scheduling one retry")
        return {
            "verification_passed": False,
            "blocked": False,
            "should_retry": True,
            "retry_count": retry_count + 1,
        }

    return {"verification_passed": False, "blocked": False, "should_retry": False}


def _node_render_reply(state: TurnState) -> TurnState:
    """Phase 6: Generate the final user-facing reply without leaking metadata."""
    intent = str(state.get("intent") or "respond")
    turn = state.get("turn") or {}
    mind = state.get("operating_mind") or {}
    pending_reply = str(state.get("pending_reply") or "")
    _log(f"node=render_reply intent={intent}")

    terminal_state = "done"
    reply_text = pending_reply
    error: dict[str, str] | None = None

    if intent == "execute":
        exec_result = state.get("execution_result") or {}
        exec_request = state.get("execution_request") or {}
        status = str(exec_result.get("status") or "unknown")
        blocked = bool(state.get("blocked"))
        verification_passed = bool(state.get("verification_passed"))

        if blocked:
            reply_text = "Waiting for your approval before proceeding."
            terminal_state = "blocked_waiting_for_user"
        elif verification_passed:
            try:
                reply_text = _generate_execution_reply(turn, mind, exec_request, exec_result)
            except Exception as exc:
                _log(f"render_reply execution summary LLM error: {exc}")
                output = str((exec_result.get("payload") or {}).get("output") or "")
                reply_text = f"Command completed.{chr(10) + output[:500] if output else ''}"
            terminal_state = "done"
        else:
            payload = exec_result.get("payload") or {}
            output = str(payload.get("output") or "")
            err_msg = output or f"Command failed (status: {status})"
            reply_text = f"The command did not complete: {err_msg[:400]}"
            terminal_state = "failed"
            error = _structured_error("execution_failed", err_msg[:400])

    elif intent == "ask_clarification":
        terminal_state = "blocked_waiting_for_user"
        if not reply_text:
            reply_text = "Could you provide more details?"

    elif intent == "escalate":
        terminal_state = "escalated"
        if not reply_text:
            reply_text = "This request requires manual review or elevated permissions."

    else:  # respond
        terminal_state = "done"
        if not reply_text:
            reply_text = "I'm here to help. What would you like to work on?"

    agent_meta = {
        "provider": str(turn.get("provider") or "anthropic"),
        "model": str(turn.get("model") or _resolve_model()),
        "sessionId": str(turn.get("sessionId") or ""),
        "orchestrationMode": "langgraph",
        "graphModel": _resolve_model(),
    }

    response = {
        "payloads": [{"text": reply_text}],
        "terminalState": terminal_state,
        "agentMeta": agent_meta,
        "stopReason": f"langgraph:{terminal_state}",
        "error": error,
    }

    return {"final_response": response}


def _node_persist_turn_artifacts(state: TurnState) -> TurnState:
    """Phase 7: Write back durable state for subsequent turns."""
    turn = state.get("turn") or {}
    response = state.get("final_response") or {}
    mind = state.get("operating_mind") or {}
    intent = str(state.get("intent") or "")

    terminal = str(response.get("terminalState") or "")
    _log(
        f"node=persist_turn_artifacts "
        f"runId={turn.get('runId')} "
        f"intent={intent} "
        f"terminal={terminal}"
    )

    # Log preserved state for observability
    user_model = mind.get("user_model") or {}
    instructions = user_model.get("standing_instructions") or []
    if instructions:
        _log(
            f"state_writeback: {len(instructions)} standing instructions "
            "preserved for next turn"
        )

    task_ctx = mind.get("task_context") or {}
    if task_ctx.get("active_objective"):
        _log("state_writeback: active task context preserved")

    _store_turn_in_mem0(turn, response)

    return {"state_written": True}


# ---------------------------------------------------------------------------
# Graph routing functions
# ---------------------------------------------------------------------------


def _route_from_decide(state: TurnState) -> str:
    intent = str(state.get("intent") or "respond")
    if intent == "execute" and state.get("execution_request"):
        return "build_execution_request"
    return "render_reply"


def _route_from_verify(state: TurnState) -> str:
    if state.get("should_retry") and int(state.get("retry_count") or 0) <= 1:
        return "diagnose_unknowns"
    return "render_reply"


# ---------------------------------------------------------------------------
# Graph compilation
# ---------------------------------------------------------------------------


def _build_graph() -> Any:
    """Build and compile the LangGraph state machine."""
    builder: StateGraph = StateGraph(TurnState)

    builder.add_node("ingest_turn", _node_ingest_turn)
    builder.add_node("reconstruct_state", _node_reconstruct_state)
    builder.add_node("diagnose_unknowns", _node_diagnose_unknowns)
    builder.add_node("decide_intent", _node_decide_intent)
    builder.add_node("build_execution_request", _node_build_execution_request)
    builder.add_node("await_host_execution", _node_await_host_execution)
    builder.add_node("verify_result", _node_verify_result)
    builder.add_node("render_reply", _node_render_reply)
    builder.add_node("persist_turn_artifacts", _node_persist_turn_artifacts)

    builder.set_entry_point("ingest_turn")
    builder.add_edge("ingest_turn", "reconstruct_state")
    builder.add_edge("reconstruct_state", "diagnose_unknowns")
    builder.add_edge("diagnose_unknowns", "decide_intent")

    builder.add_conditional_edges(
        "decide_intent",
        _route_from_decide,
        {
            "build_execution_request": "build_execution_request",
            "render_reply": "render_reply",
        },
    )

    builder.add_edge("build_execution_request", "await_host_execution")
    builder.add_edge("await_host_execution", "verify_result")

    builder.add_conditional_edges(
        "verify_result",
        _route_from_verify,
        {
            "diagnose_unknowns": "diagnose_unknowns",
            "render_reply": "render_reply",
        },
    )

    builder.add_edge("render_reply", "persist_turn_artifacts")
    builder.add_edge("persist_turn_artifacts", END)

    return builder.compile(checkpointer=_CHECKPOINTER)


def _get_graph() -> Any:
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = _build_graph()
    return _GRAPH


# ---------------------------------------------------------------------------
# RPC handlers
# ---------------------------------------------------------------------------


def _invoke_turn(request_id: str, turn: dict[str, Any]) -> dict[str, Any]:
    """Handle invoke_turn RPC: run graph from the beginning."""
    graph = _get_graph()
    config = {"configurable": {"thread_id": request_id}}
    initial_state: TurnState = {"turn": turn}

    try:
        result = graph.invoke(initial_state, config)

        # Detect LangGraph interrupt (execution needed by host)
        interrupts = result.get("__interrupt__")
        if interrupts:
            exec_request = interrupts[0].value if interrupts else {}
            _log(
                f"invoke_turn: interrupted checkpoint={request_id} "
                f"node=await_host_execution"
            )
            return {
                "status": "interrupted",
                "checkpointId": request_id,
                "graphNode": "await_host_execution",
                "executionRequest": exec_request,
            }

        # Also check via get_state for robustness
        graph_state = graph.get_state(config)
        if graph_state.next:
            exec_request = {}
            for task in (graph_state.tasks or []):
                for intr in (task.interrupts or []):
                    exec_request = intr.value
                    break
                if exec_request:
                    break
            if not exec_request:
                exec_request = result.get("execution_request") or {}
            _log(f"invoke_turn: interrupted (via get_state) checkpoint={request_id}")
            return {
                "status": "interrupted",
                "checkpointId": request_id,
                "graphNode": "await_host_execution",
                "executionRequest": exec_request,
            }

        # Completed normally
        response = result.get("final_response")
        if not response:
            response = {
                "payloads": [{"text": "Turn completed."}],
                "terminalState": "done",
                "agentMeta": {
                    "provider": str(turn.get("provider") or "anthropic"),
                    "model": _resolve_model(),
                    "sessionId": str(turn.get("sessionId") or ""),
                },
                "stopReason": "langgraph:done",
                "error": None,
            }

        _log(f"invoke_turn: completed terminal={response.get('terminalState')}")
        return {"status": "completed", "response": response}

    except Exception as exc:
        _log(f"invoke_turn error: {exc}")
        error = _structured_error("graph_error", str(exc))
        return {
            "status": "failed",
            "error": error,
            "response": {
                "payloads": [
                    {
                        "text": f"LangGraph orchestration error: {str(exc)[:200]}",
                        "isError": True,
                    }
                ],
                "terminalState": "failed",
                "agentMeta": {
                    "provider": str(turn.get("provider") or "anthropic"),
                    "model": _resolve_model(),
                    "sessionId": str(turn.get("sessionId") or ""),
                },
                "stopReason": "langgraph:failed",
                "error": error,
            },
        }


def _resume_turn(
    request_id: str,
    checkpoint_id: str,
    turn: dict[str, Any],
    execution_result: dict[str, Any],
) -> dict[str, Any]:
    """Handle resume_turn RPC: resume graph after host execution."""
    graph = _get_graph()
    config = {"configurable": {"thread_id": checkpoint_id}}

    try:
        graph_state = graph.get_state(config)
    except Exception as exc:
        _log(f"resume_turn: checkpoint lookup failed: {exc}")
        return {
            "status": "failed",
            "error": _structured_error(
                "missing_checkpoint",
                f"Checkpoint {checkpoint_id} not found: {exc}",
            ),
        }

    if not graph_state.next:
        _log(f"resume_turn: checkpoint {checkpoint_id} has no pending nodes")
        return {
            "status": "failed",
            "error": _structured_error(
                "missing_checkpoint",
                "Checkpoint has no pending execution node.",
            ),
        }

    try:
        result = graph.invoke(Command(resume=execution_result), config)

        # Check for unexpected second interrupt
        interrupts = result.get("__interrupt__")
        if interrupts:
            exec_request = interrupts[0].value if interrupts else {}
            _log(f"resume_turn: unexpected second interrupt checkpoint={checkpoint_id}")
            return {
                "status": "interrupted",
                "checkpointId": checkpoint_id,
                "graphNode": "await_host_execution",
                "executionRequest": exec_request,
            }

        graph_state_after = graph.get_state(config)
        if graph_state_after.next:
            exec_request = {}
            for task in (graph_state_after.tasks or []):
                for intr in (task.interrupts or []):
                    exec_request = intr.value
                    break
                if exec_request:
                    break
            return {
                "status": "interrupted",
                "checkpointId": checkpoint_id,
                "graphNode": "await_host_execution",
                "executionRequest": exec_request,
            }

        response = result.get("final_response")
        if not response:
            response = {
                "payloads": [{"text": "Turn completed after execution."}],
                "terminalState": "done",
                "agentMeta": {
                    "provider": str(turn.get("provider") or "anthropic"),
                    "model": _resolve_model(),
                    "sessionId": str(turn.get("sessionId") or ""),
                },
                "stopReason": "langgraph:done",
                "error": None,
            }

        _log(f"resume_turn: completed terminal={response.get('terminalState')}")
        return {"status": "completed", "response": response}

    except Exception as exc:
        _log(f"resume_turn error: {exc}")
        error = _structured_error("resume_error", str(exc))
        return {
            "status": "failed",
            "error": error,
            "response": {
                "payloads": [
                    {
                        "text": f"LangGraph resume error: {str(exc)[:200]}",
                        "isError": True,
                    }
                ],
                "terminalState": "failed",
                "agentMeta": {
                    "provider": str(turn.get("provider") or "anthropic"),
                    "model": _resolve_model(),
                    "sessionId": str(turn.get("sessionId") or ""),
                },
                "stopReason": "langgraph:failed",
                "error": error,
            },
        }


# ---------------------------------------------------------------------------
# RPC dispatch
# ---------------------------------------------------------------------------


def _handle(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch RPC method calls."""
    if method == "health":
        _get_graph()  # ensure graph is initialized
        return {"ok": True}

    if method == "shutdown":
        return {"ok": True}

    if method == "invoke_turn":
        request_id = str(params.get("requestId") or uuid.uuid4())
        turn = dict(params.get("turn") or {})
        return _invoke_turn(request_id, turn)

    if method == "resume_turn":
        request_id = str(params.get("requestId") or "")
        checkpoint_id = str(params.get("checkpointId") or "")
        turn = dict(params.get("turn") or {})
        execution_result = dict(params.get("executionResult") or {})
        return _resume_turn(request_id, checkpoint_id, turn, execution_result)

    raise ValueError(f"unknown_method:{method}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    _log("sidecar starting — initializing LangGraph graph")
    _get_graph()  # warm-up compilation
    _log(f"LangGraph graph ready model={_resolve_model()}")

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        request_id = "unknown"
        try:
            envelope = json.loads(line)
            request_id = str(envelope["id"])
            method = str(envelope["method"])
            params = dict(envelope.get("params") or {})
            result = _handle(method, params)
            _write({"id": request_id, "status": "ok", "result": result})
            if method == "shutdown":
                _log("shutdown received — exiting")
                return 0
        except Exception as exc:  # noqa: BLE001
            try:
                request_id = str(json.loads(line).get("id") or "unknown")
            except Exception:  # noqa: BLE001
                pass
            _log(f"error handling request {request_id}: {exc}")
            _write(
                {
                    "id": request_id,
                    "status": "error",
                    "error": _structured_error("sidecar_error", str(exc)),
                }
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
