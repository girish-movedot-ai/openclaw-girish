import { isRecord } from "../../utils.js";

export const MAX_GRAPH_REQUEST_BYTES = 5 * 1024 * 1024;
export const LANGGRAPH_HEALTH_TIMEOUT_MS = 2_000;
export const LANGGRAPH_TURN_TIMEOUT_MS = 30_000;

export const GRAPH_TURN_REQUEST_FIELDS = [
  "sessionId",
  "sessionKey",
  "agentId",
  "messageChannel",
  "messageProvider",
  "agentAccountId",
  "trigger",
  "memoryFlushWritePath",
  "messageTo",
  "messageThreadId",
  "groupId",
  "groupChannel",
  "groupSpace",
  "spawnedBy",
  "senderId",
  "senderName",
  "senderUsername",
  "senderE164",
  "senderIsOwner",
  "currentChannelId",
  "currentThreadTs",
  "currentMessageId",
  "replyToMode",
  "requireExplicitMessageTarget",
  "disableMessageTool",
  "allowGatewaySubagentBinding",
  "sessionFile",
  "workspaceDir",
  "agentDir",
  "config",
  "skillsSnapshot",
  "prompt",
  "images",
  "clientTools",
  "disableTools",
  "provider",
  "model",
  "authProfileId",
  "authProfileIdSource",
  "thinkLevel",
  "fastMode",
  "verboseLevel",
  "reasoningLevel",
  "toolResultFormat",
  "suppressToolErrorWarnings",
  "bootstrapContextMode",
  "bootstrapContextRunKind",
  "bootstrapPromptWarningSignaturesSeen",
  "bootstrapPromptWarningSignature",
  "execOverrides",
  "bashElevated",
  "timeoutMs",
  "runId",
  "blockReplyBreak",
  "blockReplyChunking",
  "extraSystemPrompt",
  "inputProvenance",
  "streamParams",
  "ownerNumbers",
  "enforceFinalTag",
  "allowTransientCooldownProbe",
] as const;

export const HOST_ONLY_RUN_EMBEDDED_FIELDS = [
  "hasRepliedRef",
  "abortSignal",
  "shouldEmitToolResult",
  "shouldEmitToolOutput",
  "onPartialReply",
  "onAssistantMessageStart",
  "onBlockReply",
  "onBlockReplyFlush",
  "onReasoningStream",
  "onReasoningEnd",
  "onToolResult",
  "onAgentEvent",
  "lane",
  "enqueue",
] as const;

export type GraphStructuredError = {
  kind: string;
  message: string;
};

export type GraphTurnRequest = Record<(typeof GRAPH_TURN_REQUEST_FIELDS)[number], unknown> & {
  sessionId: string;
  sessionFile: string;
  workspaceDir: string;
  prompt: string;
  timeoutMs: number;
  runId: string;
};

export type GraphExecutionIntent = "reply" | "shell" | "approval_request";

export type GraphExecutionRequest = {
  idempotencyKey: string;
  intent: GraphExecutionIntent;
  command?: string;
  cwd?: string | null;
  requiresApproval: boolean;
  verificationContract: Record<string, unknown>;
};

export type GraphExecutionResult = {
  status: "completed" | "failed" | "approval_pending" | "cancelled";
  ran: boolean;
  payload: Record<string, unknown>;
  barrier?: Record<string, unknown> | null;
  verificationEvidence?: Record<string, unknown> | null;
};

export type GraphTurnResponse = {
  payloads: Array<Record<string, unknown>>;
  terminalState: "done" | "blocked_waiting_for_user" | "escalated" | "failed";
  agentMeta?: Record<string, unknown> | null;
  stopReason?: string | null;
  error?: GraphStructuredError | null;
  pendingApprovalDescriptor?: Record<string, unknown> | null;
};

export type GraphInterrupt = {
  status: "interrupted";
  checkpointId: string;
  graphNode: "await_host_execution";
  executionRequest: GraphExecutionRequest;
};

export type GraphCompletion = {
  status: "completed";
  response: GraphTurnResponse;
};

export type GraphFailure = {
  status: "failed";
  error: GraphStructuredError;
  response?: GraphTurnResponse;
};

export type InvokeTurnResponse = GraphInterrupt | GraphCompletion | GraphFailure;
export type ResumeTurnResponse = InvokeTurnResponse;

export type InvokeTurnRequest = {
  requestId: string;
  turn: GraphTurnRequest;
};

export type ResumeTurnRequest = {
  requestId: string;
  checkpointId: string;
  turn: GraphTurnRequest;
  executionResult: GraphExecutionResult;
};

function isGraphStructuredError(value: unknown): value is GraphStructuredError {
  return (
    isRecord(value) &&
    typeof value.kind === "string" &&
    value.kind.trim().length > 0 &&
    typeof value.message === "string" &&
    value.message.trim().length > 0
  );
}

function isGraphExecutionRequest(value: unknown): value is GraphExecutionRequest {
  return (
    isRecord(value) &&
    typeof value.idempotencyKey === "string" &&
    (value.intent === "reply" || value.intent === "shell" || value.intent === "approval_request") &&
    typeof value.requiresApproval === "boolean" &&
    isRecord(value.verificationContract)
  );
}

function isGraphTurnResponse(value: unknown): value is GraphTurnResponse {
  return (
    isRecord(value) &&
    Array.isArray(value.payloads) &&
    (value.terminalState === "done" ||
      value.terminalState === "blocked_waiting_for_user" ||
      value.terminalState === "escalated" ||
      value.terminalState === "failed") &&
    (value.error === undefined || value.error === null || isGraphStructuredError(value.error))
  );
}

export function isInvokeTurnResponse(value: unknown): value is InvokeTurnResponse {
  if (!isRecord(value) || typeof value.status !== "string") {
    return false;
  }
  if (value.status === "interrupted") {
    return (
      typeof value.checkpointId === "string" &&
      value.checkpointId.trim().length > 0 &&
      value.graphNode === "await_host_execution" &&
      isGraphExecutionRequest(value.executionRequest)
    );
  }
  if (value.status === "completed") {
    return isGraphTurnResponse(value.response);
  }
  if (value.status === "failed") {
    return (
      isGraphStructuredError(value.error) &&
      (value.response === undefined ||
        value.response === null ||
        isGraphTurnResponse(value.response))
    );
  }
  return false;
}
