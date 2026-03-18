import type { OpenClawConfig } from "../../config/config.js";
import {
  loadSessionStore,
  resolveDefaultSessionStorePath,
  resolveSessionStoreEntry,
  type SessionEntry,
} from "../../config/sessions.js";
import { resolveAgentConfig, resolveSessionAgentId } from "../agent-scope.js";

export type TurnOrchestrationMode = "legacy" | "langgraph";

export function coerceTurnOrchestrationMode(value: unknown): TurnOrchestrationMode | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const normalized = value.trim().toLowerCase();
  if (normalized === "legacy" || normalized === "langgraph") {
    return normalized;
  }
  return undefined;
}

function resolveSessionTurnOrchestrationMode(params: {
  cfg?: OpenClawConfig;
  agentId?: string;
  sessionKey?: string;
  sessionEntry?: SessionEntry;
}): TurnOrchestrationMode | undefined {
  const sessionMode = coerceTurnOrchestrationMode(params.sessionEntry?.turnOrchestration);
  if (sessionMode) {
    return sessionMode;
  }
  const sessionKey = params.sessionKey?.trim();
  if (!sessionKey) {
    return undefined;
  }
  try {
    const sessionAgentId = resolveSessionAgentId({
      sessionKey,
      config: params.cfg,
    });
    const storePath = resolveDefaultSessionStorePath(params.agentId ?? sessionAgentId);
    const store = loadSessionStore(storePath);
    const resolved = resolveSessionStoreEntry({ store, sessionKey });
    return coerceTurnOrchestrationMode(resolved.existing?.turnOrchestration);
  } catch {
    return undefined;
  }
}

export function resolveTurnOrchestrationMode(params: {
  cfg?: OpenClawConfig;
  agentId?: string;
  sessionKey?: string;
  sessionEntry?: SessionEntry;
}): TurnOrchestrationMode {
  const sessionMode = resolveSessionTurnOrchestrationMode(params);
  if (sessionMode) {
    return sessionMode;
  }
  const sessionAgentId = resolveSessionAgentId({
    sessionKey: params.sessionKey,
    config: params.cfg,
    ...(params.agentId ? { agentId: params.agentId } : {}),
  });
  const agentMode = params.cfg
    ? coerceTurnOrchestrationMode(resolveAgentConfig(params.cfg, sessionAgentId)?.turnOrchestration)
    : undefined;
  if (agentMode) {
    return agentMode;
  }
  const defaultMode = coerceTurnOrchestrationMode(params.cfg?.agents?.defaults?.turnOrchestration);
  return defaultMode ?? "legacy";
}
