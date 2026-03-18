import { describe, expect, it } from "vitest";
import type { SessionEntry } from "../config/sessions.js";
import { applySessionsPatchToStore } from "./sessions-patch.js";

describe("sessions.patch turnOrchestration", () => {
  it("persists langgraph overrides", async () => {
    const store: Record<string, SessionEntry> = {};

    const result = await applySessionsPatchToStore({
      cfg: {},
      store,
      storeKey: "agent:main:main",
      patch: {
        key: "agent:main:main",
        turnOrchestration: "langgraph",
      },
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.entry.turnOrchestration).toBe("langgraph");
    }
  });

  it("rejects invalid values", async () => {
    const store: Record<string, SessionEntry> = {};

    const result = await applySessionsPatchToStore({
      cfg: {},
      store,
      storeKey: "agent:main:main",
      patch: {
        key: "agent:main:main",
        turnOrchestration: "broken" as never,
      },
    });

    expect(result.ok).toBe(false);
  });
});
