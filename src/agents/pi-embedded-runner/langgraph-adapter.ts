import fs from "node:fs";
import path from "node:path";
import { createSubsystemLogger } from "../../logging/subsystem.js";
import { requestExecApprovalDecisionForHost } from "../bash-tools.exec-approval-request.js";
import { runExecProcess } from "../bash-tools.exec-runtime.js";
import {
  GRAPH_TURN_REQUEST_FIELDS,
  HOST_ONLY_RUN_EMBEDDED_FIELDS,
  isInvokeTurnResponse,
  type GraphExecutionRequest,
  type GraphExecutionResult,
  type GraphTurnRequest,
  type GraphTurnResponse,
  type InvokeTurnRequest,
  MAX_GRAPH_REQUEST_BYTES,
  type ResumeTurnRequest,
} from "./langgraph-contract.js";
import { LANGGRAPH_TURN_TIMEOUT_MS } from "./langgraph-contract.js";
import type { GraphRpcClient } from "./langgraph-sidecar.js";
import type { RunEmbeddedPiAgentParams } from "./run/params.js";
import type { EmbeddedPiAgentMeta, EmbeddedPiRunResult } from "./types.js";

const log = createSubsystemLogger("langgraph");

type LangGraphFailureKind = "langgraph_contract" | "langgraph_failure" | "langgraph_timeout";

// ---------------------------------------------------------------------------
// Session history write-back
// ---------------------------------------------------------------------------

async function appendSessionHistory(
  sessionFile: string,
  prompt: string,
  replyText: string,
): Promise<void> {
  if (!sessionFile || !prompt.trim()) {
    return;
  }
  try {
    const dir = path.dirname(sessionFile);
    await fs.promises.mkdir(dir, { recursive: true });
    const ts = new Date().toISOString();
    const entries =
      JSON.stringify({ type: "human", content: prompt, ts }) +
      "\n" +
      JSON.stringify({ type: "ai", content: replyText, ts }) +
      "\n";
    await fs.promises.appendFile(sessionFile, entries, "utf8");
  } catch (err) {
    log.warn(`langgraph session write failed for ${sessionFile}: ${String(err)}`);
  }
}

function buildSerializableTurnRequest(params: RunEmbeddedPiAgentParams): GraphTurnRequest {
  const rawTurn = {
    sessionId: params.sessionId,
    sessionKey: params.sessionKey,
    agentId: params.agentId,
    messageChannel: params.messageChannel,
    messageProvider: params.messageProvider,
    agentAccountId: params.agentAccountId,
    trigger: params.trigger,
    memoryFlushWritePath: params.memoryFlushWritePath,
    messageTo: params.messageTo,
    messageThreadId: params.messageThreadId,
    groupId: params.groupId,
    groupChannel: params.groupChannel,
    groupSpace: params.groupSpace,
    spawnedBy: params.spawnedBy,
    senderId: params.senderId,
    senderName: params.senderName,
    senderUsername: params.senderUsername,
    senderE164: params.senderE164,
    senderIsOwner: params.senderIsOwner,
    currentChannelId: params.currentChannelId,
    currentThreadTs: params.currentThreadTs,
    currentMessageId: params.currentMessageId,
    replyToMode: params.replyToMode,
    requireExplicitMessageTarget: params.requireExplicitMessageTarget,
    disableMessageTool: params.disableMessageTool,
    allowGatewaySubagentBinding: params.allowGatewaySubagentBinding,
    sessionFile: params.sessionFile,
    workspaceDir: params.workspaceDir,
    agentDir: params.agentDir,
    config: params.config,
    skillsSnapshot: params.skillsSnapshot,
    prompt: params.prompt,
    images: params.images,
    clientTools: params.clientTools,
    disableTools: params.disableTools,
    provider: params.provider,
    model: params.model,
    authProfileId: params.authProfileId,
    authProfileIdSource: params.authProfileIdSource,
    thinkLevel: params.thinkLevel,
    fastMode: params.fastMode,
    verboseLevel: params.verboseLevel,
    reasoningLevel: params.reasoningLevel,
    toolResultFormat: params.toolResultFormat,
    suppressToolErrorWarnings: params.suppressToolErrorWarnings,
    bootstrapContextMode: params.bootstrapContextMode,
    bootstrapContextRunKind: params.bootstrapContextRunKind,
    bootstrapPromptWarningSignaturesSeen: params.bootstrapPromptWarningSignaturesSeen,
    bootstrapPromptWarningSignature: params.bootstrapPromptWarningSignature,
    execOverrides: params.execOverrides,
    bashElevated: params.bashElevated,
    timeoutMs: params.timeoutMs,
    runId: params.runId,
    blockReplyBreak: params.blockReplyBreak,
    blockReplyChunking: params.blockReplyChunking,
    extraSystemPrompt: params.extraSystemPrompt,
    inputProvenance: params.inputProvenance,
    streamParams: params.streamParams,
    ownerNumbers: params.ownerNumbers,
    enforceFinalTag: params.enforceFinalTag,
    allowTransientCooldownProbe: params.allowTransientCooldownProbe,
  } satisfies GraphTurnRequest;

  const seen = new WeakSet<object>();
  const serialized = JSON.stringify(rawTurn, (_key, value) => {
    if (value === undefined) {
      return null;
    }
    if (typeof value === "function" || typeof value === "symbol" || typeof value === "bigint") {
      throw new Error("LangGraph request contains a non-serializable field.");
    }
    if (value && typeof value === "object") {
      if (seen.has(value)) {
        throw new Error("LangGraph request contains a circular reference.");
      }
      seen.add(value);
    }
    return value;
  });
  const byteLength = Buffer.byteLength(serialized, "utf8");
  if (byteLength > MAX_GRAPH_REQUEST_BYTES) {
    throw new Error(
      `LangGraph request exceeds the 5 MiB limit (${String(byteLength)} bytes serialized).`,
    );
  }
  return JSON.parse(serialized) as GraphTurnRequest;
}

function resolveFailureKind(message: string): LangGraphFailureKind {
  if (
    message.includes("invalid") ||
    message.includes("Unknown LangGraph execution intent") ||
    message.includes("non-serializable") ||
    message.includes("circular reference")
  ) {
    return "langgraph_contract";
  }
  return message.includes("timed out") ? "langgraph_timeout" : "langgraph_failure";
}

function emitLangGraphEvent(
  params: RunEmbeddedPiAgentParams,
  event: string,
  extra: Record<string, unknown> = {},
): void {
  params.onAgentEvent?.({
    stream: "langgraph",
    data: {
      traceId: params.runId,
      sessionId: params.sessionId,
      agentId: params.agentId,
      orchestrationMode: "langgraph",
      event,
      ...extra,
    },
  });
}

function buildErrorResult(params: {
  turn: GraphTurnRequest;
  durationMs: number;
  kind: LangGraphFailureKind;
  message: string;
  stopReason: string;
}): EmbeddedPiRunResult {
  const provider =
    typeof params.turn.provider === "string" && params.turn.provider
      ? params.turn.provider
      : "langgraph";
  const model =
    typeof params.turn.model === "string" && params.turn.model ? params.turn.model : "langgraph";
  return {
    payloads: [{ text: params.message, isError: true }],
    meta: {
      durationMs: params.durationMs,
      agentMeta: {
        sessionId: params.turn.sessionId,
        provider,
        model,
      },
      error: {
        kind: params.kind,
        message: params.message,
      },
      stopReason: params.stopReason,
    },
  };
}

function mapPayloads(response: GraphTurnResponse): EmbeddedPiRunResult["payloads"] {
  const mapped = response.payloads
    .map((payload) => {
      const text = typeof payload.text === "string" ? payload.text : undefined;
      const mediaUrl = typeof payload.mediaUrl === "string" ? payload.mediaUrl : undefined;
      const mediaUrls = Array.isArray(payload.mediaUrls)
        ? payload.mediaUrls.filter((value): value is string => typeof value === "string")
        : undefined;
      const replyToId =
        typeof payload.replyToId === "string" || typeof payload.replyToId === "number"
          ? String(payload.replyToId)
          : undefined;
      const isError = payload.isError === true;
      if (!text && !mediaUrl && (!mediaUrls || mediaUrls.length === 0)) {
        return null;
      }
      return {
        ...(text ? { text } : {}),
        ...(mediaUrl ? { mediaUrl } : {}),
        ...(mediaUrls && mediaUrls.length > 0 ? { mediaUrls } : {}),
        ...(replyToId ? { replyToId } : {}),
        ...(isError ? { isError: true } : {}),
      };
    })
    .filter(
      (value): value is NonNullable<NonNullable<EmbeddedPiRunResult["payloads"]>[number]> =>
        value !== null,
    );
  return mapped.length > 0 ? mapped : undefined;
}

function mapCompletionToResult(params: {
  turn: GraphTurnRequest;
  response: GraphTurnResponse;
  durationMs: number;
}): EmbeddedPiRunResult {
  const agentMetaSource = params.response.agentMeta ?? {};
  const provider =
    typeof agentMetaSource.provider === "string" && agentMetaSource.provider
      ? agentMetaSource.provider
      : typeof params.turn.provider === "string" && params.turn.provider
        ? params.turn.provider
        : "langgraph";
  const model =
    typeof agentMetaSource.model === "string" && agentMetaSource.model
      ? agentMetaSource.model
      : typeof params.turn.model === "string" && params.turn.model
        ? params.turn.model
        : "langgraph";
  const payloads = mapPayloads(params.response);
  const errorMessage = params.response.error?.message;
  return {
    payloads:
      payloads ??
      (errorMessage ? [{ text: errorMessage, isError: true }] : [{ text: "LangGraph completed." }]),
    meta: {
      durationMs: params.durationMs,
      agentMeta: {
        sessionId: params.turn.sessionId,
        provider,
        model,
      } satisfies EmbeddedPiAgentMeta,
      ...(params.response.error
        ? {
            error: {
              kind:
                params.response.terminalState === "failed"
                  ? ("langgraph_failure" satisfies LangGraphFailureKind)
                  : ("langgraph_contract" satisfies LangGraphFailureKind),
              message: params.response.error.message,
            },
          }
        : {}),
      stopReason: params.response.stopReason ?? `langgraph:${params.response.terminalState}`,
    },
  };
}

async function emitVisiblePayloads(
  params: RunEmbeddedPiAgentParams,
  payloads: NonNullable<EmbeddedPiRunResult["payloads"]> | undefined,
): Promise<void> {
  if (!payloads || payloads.length === 0) {
    return;
  }
  await params.onAssistantMessageStart?.();
  for (const payload of payloads) {
    const mediaUrls = payload.mediaUrls ?? (payload.mediaUrl ? [payload.mediaUrl] : undefined);
    if (payload.text || mediaUrls) {
      await params.onPartialReply?.({
        ...(payload.text ? { text: payload.text } : {}),
        ...(mediaUrls ? { mediaUrls } : {}),
      });
      await params.onBlockReply?.({
        ...(payload.text ? { text: payload.text } : {}),
        ...(mediaUrls ? { mediaUrls } : {}),
        ...(payload.isError ? { isError: true } : {}),
      });
      params.onAgentEvent?.({
        stream: "assistant",
        data: {
          ...(payload.text ? { text: payload.text, delta: payload.text } : {}),
          ...(mediaUrls ? { mediaUrls } : {}),
        },
      });
    }
  }
}

function resolveShellEnv(): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (typeof value === "string") {
      env[key] = value;
    }
  }
  return env;
}

async function executeShellRequest(
  request: GraphExecutionRequest,
  params: RunEmbeddedPiAgentParams,
): Promise<GraphExecutionResult> {
  if (!request.command?.trim()) {
    return {
      status: "failed",
      ran: false,
      payload: { error: "LangGraph shell request is missing a command." },
    };
  }
  if (request.requiresApproval) {
    const decision = await requestExecApprovalDecisionForHost({
      approvalId: request.idempotencyKey,
      command: request.command,
      workdir: request.cwd ?? params.workspaceDir,
      host: "gateway",
      security: "full",
      ask: "always",
      agentId: params.agentId,
      sessionKey: params.sessionKey,
      turnSourceChannel: params.messageChannel,
      turnSourceTo: params.messageTo,
      turnSourceAccountId: params.agentAccountId,
      turnSourceThreadId: params.messageThreadId,
    });
    if (decision !== "allow-once" && decision !== "allow-always") {
      return {
        status: "cancelled",
        ran: false,
        payload: { decision: decision ?? "timed_out" },
        barrier: { approvalRequired: true },
      };
    }
  }
  const handle = await runExecProcess({
    command: request.command,
    workdir: request.cwd ?? params.workspaceDir,
    env: resolveShellEnv(),
    sandbox: undefined,
    containerWorkdir: null,
    usePty: false,
    warnings: [],
    maxOutput: 8_000,
    pendingMaxOutput: 8_000,
    notifyOnExit: false,
    notifyOnExitEmptySuccess: false,
    timeoutSec: Math.max(1, Math.ceil(params.timeoutMs / 1000)),
  });
  const outcome = await handle.promise;
  const completed = !outcome.timedOut && outcome.exitCode === 0;
  return {
    status: completed ? "completed" : "failed",
    ran: true,
    payload: {
      output: outcome.aggregated,
      exitCode: outcome.exitCode,
      timedOut: outcome.timedOut,
    },
    verificationEvidence: request.verificationContract,
  };
}

async function executeGraphRequest(
  request: GraphExecutionRequest,
  params: RunEmbeddedPiAgentParams,
): Promise<GraphExecutionResult> {
  if (request.intent === "shell") {
    return await executeShellRequest(request, params);
  }
  if (request.intent === "approval_request") {
    return {
      status: "approval_pending",
      ran: false,
      payload: {
        error: "Standalone approval_request intents are not supported in v1.",
      },
    };
  }
  throw new Error(`Unknown LangGraph execution intent: ${request.intent}`);
}

export async function runLangGraphTurn(
  params: RunEmbeddedPiAgentParams,
  deps: {
    sidecar: GraphRpcClient;
  },
): Promise<EmbeddedPiRunResult> {
  const startedAt = Date.now();
  const turn = buildSerializableTurnRequest(params);
  emitLangGraphEvent(params, "turn_start", {
    graphNode: "ingest_turn",
  });
  log.info(
    `langgraph turn start runId=${params.runId} sessionId=${params.sessionId} agentId=${params.agentId ?? "main"}`,
  );
  try {
    let response = await deps.sidecar.invokeTurn(
      {
        requestId: params.runId,
        turn,
      } satisfies InvokeTurnRequest,
      LANGGRAPH_TURN_TIMEOUT_MS,
    );
    let interruptsSeen = 0;
    while (true) {
      if (!isInvokeTurnResponse(response)) {
        throw new Error("LangGraph sidecar returned an invalid turn response.");
      }
      if (response.status === "completed") {
        emitLangGraphEvent(params, "turn_complete", {
          terminalState: response.response.terminalState,
        });
        log.info(
          `langgraph turn complete runId=${params.runId} terminalState=${response.response.terminalState}`,
        );
        const result = mapCompletionToResult({
          turn,
          response: response.response,
          durationMs: Date.now() - startedAt,
        });
        await emitVisiblePayloads(params, result.payloads);
        // Write user message + assistant reply to session JSONL for operating
        // mind reconstruction on the next turn (Phase 7 write-back, TS side).
        const replyText = result.payloads?.find((p) => p.text && !p.isError)?.text ?? "";
        if (params.sessionFile && replyText) {
          await appendSessionHistory(params.sessionFile, params.prompt, replyText);
        }
        return result;
      }
      if (response.status === "failed") {
        emitLangGraphEvent(params, "turn_failed", {
          terminalState: response.response?.terminalState ?? "failed",
          errorKind: response.error.kind,
        });
        const result = buildErrorResult({
          turn,
          durationMs: Date.now() - startedAt,
          kind: "langgraph_failure",
          message: response.error.message,
          stopReason: "langgraph:failed",
        });
        await emitVisiblePayloads(params, result.payloads);
        return result;
      }
      interruptsSeen += 1;
      if (interruptsSeen > 2) {
        throw new Error("LangGraph interrupt retry limit exceeded.");
      }
      emitLangGraphEvent(params, "host_execution", {
        graphNode: response.graphNode,
        intent: response.executionRequest.intent,
      });
      const executionResult = await executeGraphRequest(response.executionRequest, params);
      response = await deps.sidecar.resumeTurn(
        {
          requestId: params.runId,
          checkpointId: response.checkpointId,
          turn,
          executionResult,
        } satisfies ResumeTurnRequest,
        LANGGRAPH_TURN_TIMEOUT_MS,
      );
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    const kind = resolveFailureKind(message);
    emitLangGraphEvent(params, "turn_failed", {
      errorKind: kind,
      message,
    });
    log.error(`langgraph turn failed runId=${params.runId} error=${message}`);
    const result = buildErrorResult({
      turn,
      durationMs: Date.now() - startedAt,
      kind,
      message,
      stopReason: "langgraph:error",
    });
    await emitVisiblePayloads(params, result.payloads);
    return result;
  }
}

export { buildSerializableTurnRequest, GRAPH_TURN_REQUEST_FIELDS, HOST_ONLY_RUN_EMBEDDED_FIELDS };
