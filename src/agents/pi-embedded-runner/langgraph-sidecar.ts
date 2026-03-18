import path from "node:path";
import { resolveOpenClawPackageRoot } from "../../infra/openclaw-root.js";
import { createSubsystemLogger } from "../../logging/subsystem.js";
import { createChildAdapter, type ChildAdapter } from "../../process/supervisor/adapters/child.js";
import type {
  InvokeTurnRequest,
  InvokeTurnResponse,
  ResumeTurnRequest,
  ResumeTurnResponse,
} from "./langgraph-contract.js";
import { isInvokeTurnResponse, LANGGRAPH_HEALTH_TIMEOUT_MS } from "./langgraph-contract.js";

const log = createSubsystemLogger("langgraph");
const LANGGRAPH_RPC_SINGLETON_KEY = "__openclawLanggraphRpcClient";

type PendingRpc = {
  resolve: (value: unknown) => void;
  reject: (reason?: unknown) => void;
  timer: ReturnType<typeof setTimeout>;
};

type RpcEnvelope =
  | { id: string; method: string; params: Record<string, unknown> }
  | {
      id: string;
      status: "ok";
      result: unknown;
    }
  | {
      id: string;
      status: "error";
      error: { kind: string; message: string };
    };

export type GraphRpcClient = {
  ensureStarted(): Promise<void>;
  health(timeoutMs?: number): Promise<{ ok: boolean }>;
  invokeTurn(request: InvokeTurnRequest, timeoutMs: number): Promise<InvokeTurnResponse>;
  resumeTurn(request: ResumeTurnRequest, timeoutMs: number): Promise<ResumeTurnResponse>;
  stop(): Promise<void>;
};

async function resolveSidecarEntrypoint(): Promise<string> {
  const packageRoot = await resolveOpenClawPackageRoot({
    moduleUrl: import.meta.url,
    argv1: process.argv[1],
    cwd: process.cwd(),
  });
  if (!packageRoot) {
    throw new Error("OpenClaw package root not found for LangGraph sidecar.");
  }
  return path.join(packageRoot, "assets", "langgraph-turn-orchestrator-sidecar", "main.py");
}

class ManagedGraphRpcClient implements GraphRpcClient {
  private child: ChildAdapter | undefined;
  private lineBuffer = "";
  private pending = new Map<string, PendingRpc>();
  private startPromise: Promise<void> | undefined;
  private healthy = false;
  private everStarted = false;
  private restartBudgetUsed = false;

  async ensureStarted(): Promise<void> {
    if (this.child && this.healthy) {
      return;
    }
    if (this.startPromise) {
      await this.startPromise;
      return;
    }
    this.startPromise = this.startChild();
    try {
      await this.startPromise;
    } finally {
      this.startPromise = undefined;
    }
  }

  async health(timeoutMs: number = LANGGRAPH_HEALTH_TIMEOUT_MS): Promise<{ ok: boolean }> {
    const result = await this.sendRpc("health", {}, timeoutMs);
    if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true) {
      return { ok: false };
    }
    return { ok: true };
  }

  async invokeTurn(request: InvokeTurnRequest, timeoutMs: number): Promise<InvokeTurnResponse> {
    const result = await this.sendRpc("invoke_turn", request as Record<string, unknown>, timeoutMs);
    if (!isInvokeTurnResponse(result)) {
      throw new Error("LangGraph sidecar returned an invalid invoke_turn response.");
    }
    return result;
  }

  async resumeTurn(request: ResumeTurnRequest, timeoutMs: number): Promise<ResumeTurnResponse> {
    const result = await this.sendRpc("resume_turn", request as Record<string, unknown>, timeoutMs);
    if (!isInvokeTurnResponse(result)) {
      throw new Error("LangGraph sidecar returned an invalid resume_turn response.");
    }
    return result;
  }

  async stop(): Promise<void> {
    const child = this.child;
    if (!child) {
      return;
    }
    if (child.stdin && !child.stdin.destroyed) {
      try {
        child.stdin.write(
          JSON.stringify({
            id: `shutdown-${Date.now().toString(36)}`,
            method: "shutdown",
            params: {},
          }) + "\n",
        );
      } catch {
        // Best-effort.
      }
    }
    this.healthy = false;
    child.kill("SIGTERM");
    const waitPromise = child.wait().catch(() => ({ code: null, signal: null }));
    const result = await Promise.race([
      waitPromise,
      new Promise<null>((resolve) => setTimeout(() => resolve(null), 5_000)),
    ]);
    if (result === null) {
      child.kill("SIGKILL");
      await waitPromise.catch(() => undefined);
    }
    this.disposeChild();
  }

  private async startChild(): Promise<void> {
    if (this.everStarted && this.restartBudgetUsed) {
      throw new Error("LangGraph sidecar restart budget exhausted.");
    }
    if (this.everStarted) {
      this.restartBudgetUsed = true;
      log.warn("langgraph sidecar restart requested before a new turn");
    }
    const entrypoint = await resolveSidecarEntrypoint();
    const python = process.env.OPENCLAW_LANGGRAPH_PYTHON?.trim() || "python3";
    const child = await createChildAdapter({
      argv: [python, "-u", entrypoint],
      stdinMode: "pipe-open",
    });
    this.child = child;
    this.lineBuffer = "";
    this.pending.clear();
    this.healthy = true;
    this.everStarted = true;
    child.onStdout((chunk) => {
      this.handleStdout(chunk);
    });
    child.onStderr((chunk) => {
      const message = chunk.trim();
      if (message) {
        log.info(`[langgraph-sidecar] ${message}`);
      }
    });
    void child
      .wait()
      .then(({ code, signal }) => {
        this.handleChildExit(
          `LangGraph sidecar exited (code=${String(code ?? "null")} signal=${String(signal ?? "null")})`,
        );
      })
      .catch((err) => {
        this.handleChildExit(`LangGraph sidecar failed: ${String(err)}`);
      });
    log.info(`langgraph sidecar started pid=${String(child.pid ?? "unknown")}`);
  }

  private handleStdout(chunk: string): void {
    this.lineBuffer += chunk;
    while (true) {
      const newlineIndex = this.lineBuffer.indexOf("\n");
      if (newlineIndex < 0) {
        return;
      }
      const line = this.lineBuffer.slice(0, newlineIndex).trim();
      this.lineBuffer = this.lineBuffer.slice(newlineIndex + 1);
      if (!line) {
        continue;
      }
      this.handleEnvelope(line);
    }
  }

  private handleEnvelope(line: string): void {
    let parsed: RpcEnvelope;
    try {
      parsed = JSON.parse(line) as RpcEnvelope;
    } catch (err) {
      log.warn(`langgraph sidecar emitted invalid JSON: ${String(err)}`);
      return;
    }
    if (!("id" in parsed) || typeof parsed.id !== "string") {
      return;
    }
    const pending = this.pending.get(parsed.id);
    if (!pending) {
      return;
    }
    clearTimeout(pending.timer);
    this.pending.delete(parsed.id);
    if ("status" in parsed && parsed.status === "error") {
      pending.reject(new Error(`${parsed.error.kind}: ${parsed.error.message}`));
      return;
    }
    if ("status" in parsed && parsed.status === "ok") {
      pending.resolve(parsed.result);
    }
  }

  private handleChildExit(message: string): void {
    if (!this.child) {
      return;
    }
    this.healthy = false;
    log.error(message);
    for (const [id, pending] of this.pending) {
      clearTimeout(pending.timer);
      pending.reject(new Error(message));
      this.pending.delete(id);
    }
    this.disposeChild();
  }

  private disposeChild(): void {
    this.child?.dispose();
    this.child = undefined;
    this.lineBuffer = "";
  }

  private async sendRpc(
    method: string,
    params: Record<string, unknown>,
    timeoutMs: number,
  ): Promise<unknown> {
    await this.ensureStarted();
    const child = this.child;
    if (!child?.stdin || child.stdin.destroyed) {
      throw new Error("LangGraph sidecar stdin is unavailable.");
    }
    const id = `lg-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    const payload = JSON.stringify({ id, method, params }) + "\n";
    return await new Promise<unknown>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`LangGraph ${method} timed out after ${String(timeoutMs)}ms.`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      child.stdin?.write(payload, (err?: Error | null) => {
        if (!err) {
          return;
        }
        clearTimeout(timer);
        this.pending.delete(id);
        reject(err);
      });
    });
  }
}

export function getLangGraphRpcClient(): GraphRpcClient {
  const globalState = globalThis as typeof globalThis & {
    __openclawLanggraphRpcClient?: GraphRpcClient;
  };
  if (!globalState[LANGGRAPH_RPC_SINGLETON_KEY]) {
    globalState[LANGGRAPH_RPC_SINGLETON_KEY] = new ManagedGraphRpcClient();
  }
  return globalState[LANGGRAPH_RPC_SINGLETON_KEY] as GraphRpcClient;
}

export async function stopLangGraphRpcClient(): Promise<void> {
  await getLangGraphRpcClient().stop();
}
