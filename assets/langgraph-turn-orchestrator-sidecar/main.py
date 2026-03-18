#!/usr/bin/env python3

import json
import os
import sys
import time
import uuid
from typing import Any


CHECKPOINTS: dict[str, dict[str, Any]] = {}


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _text_payload(text: str, is_error: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"text": text}
    if is_error:
        payload["isError"] = True
    return payload


def _structured_error(kind: str, message: str) -> dict[str, str]:
    return {"kind": kind, "message": message}


def _response(
    terminal_state: str,
    text: str,
    turn: dict[str, Any],
    is_error: bool = False,
    error: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "payloads": [_text_payload(text, is_error=is_error)],
        "terminalState": terminal_state,
        "agentMeta": {
            "provider": turn.get("provider") or "langgraph",
            "model": turn.get("model") or "langgraph",
        },
        "stopReason": f"langgraph:{terminal_state}",
        "error": error,
    }


def _extract_exact_reply(prompt: str) -> str | None:
    marker = "reply exactly with "
    lowered = prompt.lower()
    idx = lowered.find(marker)
    if idx < 0:
        return None
    return prompt[idx + len(marker) :].strip() or None


def _decide(turn: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    prompt = str(turn.get("prompt") or "").strip()
    lowered = prompt.lower()
    if lowered.startswith("clarify:"):
        return "ask_clarification", None
    if lowered.startswith("escalate:"):
        return "escalate", None
    if lowered.startswith("approve:"):
        command = prompt.split(":", 1)[1].strip()
        return (
            "execute",
            {
                "idempotencyKey": turn["runId"],
                "intent": "shell",
                "command": command,
                "cwd": turn.get("workspaceDir"),
                "requiresApproval": True,
                "verificationContract": {"expectExitCode": 0},
            },
        )
    if lowered.startswith("shell:"):
        command = prompt.split(":", 1)[1].strip()
        return (
            "execute",
            {
                "idempotencyKey": turn["runId"],
                "intent": "shell",
                "command": command,
                "cwd": turn.get("workspaceDir"),
                "requiresApproval": False,
                "verificationContract": {"expectExitCode": 0},
            },
        )
    return "respond", None


def _invoke_turn(turn: dict[str, Any]) -> dict[str, Any]:
    test_mode = os.environ.get("OPENCLAW_LANGGRAPH_TEST_MODE", "").strip().lower()
    if test_mode == "stall_invoke":
        time.sleep(35)
    if test_mode == "crash_invoke":
        sys.stderr.write("langgraph test mode crash_invoke\n")
        sys.stderr.flush()
        os._exit(17)

    intent, execution_request = _decide(turn)
    prompt = str(turn.get("prompt") or "").strip()
    if intent == "respond":
        exact = _extract_exact_reply(prompt)
        reply_text = exact or f"LangGraph response: {prompt or 'OK'}"
        return {
            "status": "completed",
            "response": _response("done", reply_text, turn),
        }
    if intent == "ask_clarification":
        return {
            "status": "completed",
            "response": _response(
                "blocked_waiting_for_user",
                f"Need clarification: {prompt.split(':', 1)[1].strip() or 'please add more detail.'}",
                turn,
            ),
        }
    if intent == "escalate":
        return {
            "status": "completed",
            "response": _response(
                "escalated",
                f"Escalated by LangGraph: {prompt.split(':', 1)[1].strip() or 'manual review required.'}",
                turn,
            ),
        }

    checkpoint_id = str(uuid.uuid4())
    CHECKPOINTS[checkpoint_id] = {
        "turn": turn,
        "executionRequest": execution_request,
        "retryCount": 0,
    }
    return {
        "status": "interrupted",
        "checkpointId": checkpoint_id,
        "graphNode": "await_host_execution",
        "executionRequest": execution_request,
    }


def _resume_turn(checkpoint_id: str, turn: dict[str, Any], execution_result: dict[str, Any]) -> dict[str, Any]:
    state = CHECKPOINTS.get(checkpoint_id)
    if not state:
        return {
            "status": "failed",
            "error": _structured_error("missing_checkpoint", "LangGraph checkpoint not found for resume."),
            "response": _response(
                "failed",
                "LangGraph checkpoint not found for resume.",
                turn,
                is_error=True,
                error=_structured_error("missing_checkpoint", "LangGraph checkpoint not found for resume."),
            ),
        }

    if execution_result.get("status") == "approval_pending":
        return {
            "status": "completed",
            "response": {
                **_response(
                    "blocked_waiting_for_user",
                    "LangGraph is waiting for approval.",
                    turn,
                ),
                "pendingApprovalDescriptor": {"checkpointId": checkpoint_id},
            },
        }

    output = str(execution_result.get("payload", {}).get("output") or "").strip()
    if execution_result.get("status") == "completed":
        CHECKPOINTS.pop(checkpoint_id, None)
        text = "LangGraph command completed."
        if output:
            text = f"{text}\n{output}"
        return {
            "status": "completed",
            "response": _response("done", text, turn),
        }

    if state.get("retryCount", 0) < 1 and "retry-once" in str(turn.get("prompt") or "").lower():
        state["retryCount"] = 1
        retry_checkpoint = str(uuid.uuid4())
        CHECKPOINTS[retry_checkpoint] = state
        CHECKPOINTS.pop(checkpoint_id, None)
        return {
            "status": "interrupted",
            "checkpointId": retry_checkpoint,
            "graphNode": "await_host_execution",
            "executionRequest": state["executionRequest"],
        }

    CHECKPOINTS.pop(checkpoint_id, None)
    message = output or "LangGraph execution failed."
    return {
        "status": "failed",
        "error": _structured_error("execution_failed", message),
        "response": _response(
            "failed",
            message,
            turn,
            is_error=True,
            error=_structured_error("execution_failed", message),
        ),
    }


def _handle(method: str, params: dict[str, Any]) -> dict[str, Any]:
    if method == "health":
        return {"ok": True}
    if method == "shutdown":
        return {"ok": True}
    if method == "invoke_turn":
        return _invoke_turn(dict(params.get("turn") or {}))
    if method == "resume_turn":
        return _resume_turn(
            str(params.get("checkpointId") or ""),
            dict(params.get("turn") or {}),
            dict(params.get("executionResult") or {}),
        )
    raise ValueError(f"unknown_method:{method}")


def main() -> int:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            envelope = json.loads(line)
            request_id = str(envelope["id"])
            method = str(envelope["method"])
            params = dict(envelope.get("params") or {})
            result = _handle(method, params)
            _write({"id": request_id, "status": "ok", "result": result})
            if method == "shutdown":
                return 0
        except Exception as exc:  # noqa: BLE001
            request_id = None
            try:
                request_id = str(json.loads(line).get("id"))
            except Exception:  # noqa: BLE001
                request_id = "unknown"
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
