import { mkdtempSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import {
  buildSerializableTurnRequest,
  GRAPH_TURN_REQUEST_FIELDS,
  HOST_ONLY_RUN_EMBEDDED_FIELDS,
  runLangGraphTurn,
} from "./langgraph-adapter.js";
import type { GraphRpcClient } from "./langgraph-sidecar.js";
import type { RunEmbeddedPiAgentParams } from "./run/params.js";

function createParams(overrides: Partial<RunEmbeddedPiAgentParams> = {}): RunEmbeddedPiAgentParams {
  const workspaceDir = mkdtempSync(path.join(os.tmpdir(), "openclaw-langgraph-"));
  return {
    sessionId: "session-1",
    sessionKey: "agent:main:main",
    agentId: "main",
    messageChannel: "webchat",
    messageProvider: "webchat",
    agentAccountId: "default",
    trigger: "user",
    memoryFlushWritePath: "memory.txt",
    messageTo: "webchat:user",
    messageThreadId: "thread-1",
    groupId: "group-1",
    groupChannel: "#general",
    groupSpace: "workspace",
    spawnedBy: "agent:main:parent",
    senderId: "user-1",
    senderName: "User",
    senderUsername: "user",
    senderE164: "+15555550123",
    senderIsOwner: true,
    currentChannelId: "chan-1",
    currentThreadTs: "ts-1",
    currentMessageId: "msg-1",
    replyToMode: "all",
    hasRepliedRef: { value: false },
    requireExplicitMessageTarget: true,
    disableMessageTool: false,
    allowGatewaySubagentBinding: true,
    sessionFile: path.join(workspaceDir, "session.jsonl"),
    workspaceDir,
    agentDir: workspaceDir,
    config: { agents: { defaults: { turnOrchestration: "langgraph" } } } as never,
    skillsSnapshot: { prompt: "skills", skills: [] } as never,
    prompt: "reply exactly with LG_OK",
    images: [],
    clientTools: [],
    disableTools: false,
    provider: "openai",
    model: "gpt-5.2",
    authProfileId: "default",
    authProfileIdSource: "user",
    thinkLevel: "off",
    fastMode: false,
    verboseLevel: "off",
    reasoningLevel: "off",
    toolResultFormat: "markdown",
    suppressToolErrorWarnings: false,
    bootstrapContextMode: "full",
    bootstrapContextRunKind: "default",
    bootstrapPromptWarningSignaturesSeen: ["sig-1"],
    bootstrapPromptWarningSignature: "sig-1",
    execOverrides: { host: "gateway", security: "allowlist", ask: "always" },
    bashElevated: { enabled: false, allowed: false, defaultLevel: "off" },
    timeoutMs: 30_000,
    runId: "run-1",
    abortSignal: undefined,
    shouldEmitToolResult: () => true,
    shouldEmitToolOutput: () => true,
    onPartialReply: async () => undefined,
    onAssistantMessageStart: async () => undefined,
    onBlockReply: async () => undefined,
    onBlockReplyFlush: async () => undefined,
    blockReplyBreak: "message_end",
    blockReplyChunking: { maxChars: 100 } as never,
    onReasoningStream: async () => undefined,
    onReasoningEnd: async () => undefined,
    onToolResult: async () => undefined,
    onAgentEvent: () => undefined,
    lane: "main",
    enqueue: undefined,
    extraSystemPrompt: "Be helpful.",
    inputProvenance: { source: "test" } as never,
    streamParams: { temperature: 0 } as never,
    ownerNumbers: ["+15555550123"],
    enforceFinalTag: false,
    allowTransientCooldownProbe: false,
    ...overrides,
  };
}

describe("buildSerializableTurnRequest", () => {
  it("keeps mapped fields and strips host-only fields", () => {
    const params = createParams();

    const turn = buildSerializableTurnRequest(params);

    expect(Object.keys(turn).toSorted()).toEqual([...GRAPH_TURN_REQUEST_FIELDS].toSorted());
    for (const field of HOST_ONLY_RUN_EMBEDDED_FIELDS) {
      expect(field in turn).toBe(false);
    }
    expect(turn.prompt).toBe("reply exactly with LG_OK");
    expect(turn.workspaceDir).toBe(params.workspaceDir);
  });
});

describe("runLangGraphTurn", () => {
  it("returns completed payloads for direct responses", async () => {
    const params = createParams();
    const sidecar: GraphRpcClient = {
      ensureStarted: async () => undefined,
      health: async () => ({ ok: true }),
      invokeTurn: async () => ({
        status: "completed",
        response: {
          payloads: [{ text: "LG_OK" }],
          terminalState: "done",
          agentMeta: { provider: "openai", model: "gpt-5.2" },
          stopReason: "langgraph:done",
        },
      }),
      resumeTurn: async () => {
        throw new Error("resume not expected");
      },
      stop: async () => undefined,
    };

    const result = await runLangGraphTurn(params, { sidecar });

    expect(result.payloads?.[0]?.text).toBe("LG_OK");
    expect(result.meta.stopReason).toBe("langgraph:done");
  });

  it("executes shell interrupts through the host runtime", async () => {
    const params = createParams({ prompt: "shell: printf LG_EXEC_OK" });
    const sidecar: GraphRpcClient = {
      ensureStarted: async () => undefined,
      health: async () => ({ ok: true }),
      invokeTurn: async () => ({
        status: "interrupted",
        checkpointId: "checkpoint-1",
        graphNode: "await_host_execution",
        executionRequest: {
          idempotencyKey: "run-1",
          intent: "shell",
          command: "printf LG_EXEC_OK",
          cwd: params.workspaceDir,
          requiresApproval: false,
          verificationContract: { expectExitCode: 0 },
        },
      }),
      resumeTurn: async (request) => {
        const output = String(request.executionResult.payload.output ?? "").trim();
        expect(request.executionResult.status).toBe("completed");
        return {
          status: "completed",
          response: {
            payloads: [{ text: output }],
            terminalState: "done",
            agentMeta: { provider: "openai", model: "gpt-5.2" },
            stopReason: "langgraph:done",
          },
        };
      },
      stop: async () => undefined,
    };

    const result = await runLangGraphTurn(params, { sidecar });

    expect(result.payloads?.[0]?.text).toContain("LG_EXEC_OK");
  });

  it("fails closed for invalid sidecar responses", async () => {
    const params = createParams();
    const sidecar: GraphRpcClient = {
      ensureStarted: async () => undefined,
      health: async () => ({ ok: true }),
      invokeTurn: async () => ({ status: "weird" }) as never,
      resumeTurn: async () => {
        throw new Error("resume not expected");
      },
      stop: async () => undefined,
    };

    const result = await runLangGraphTurn(params, { sidecar });

    expect(result.payloads?.[0]?.isError).toBe(true);
    expect(result.meta.error?.kind).toBe("langgraph_contract");
  });
});
