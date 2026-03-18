import { describe, expect, it } from "vitest";

describe("resolveTurnOrchestrationMode", () => {
  it("prefers the session entry override", async () => {
    const { resolveTurnOrchestrationMode } = await import("./langgraph-mode.js");

    expect(
      resolveTurnOrchestrationMode({
        sessionEntry: { turnOrchestration: "langgraph" } as never,
      }),
    ).toBe("langgraph");
  });

  it("prefers the per-agent override over defaults", async () => {
    const { resolveTurnOrchestrationMode } = await import("./langgraph-mode.js");

    expect(
      resolveTurnOrchestrationMode({
        cfg: {
          agents: {
            defaults: {
              turnOrchestration: "legacy",
            },
            list: [
              {
                id: "main",
                turnOrchestration: "langgraph",
              },
            ],
          },
        },
        agentId: "main",
      }),
    ).toBe("langgraph");
  });

  it("fails closed to legacy for invalid values", async () => {
    const { resolveTurnOrchestrationMode } = await import("./langgraph-mode.js");

    expect(
      resolveTurnOrchestrationMode({
        cfg: {
          agents: {
            defaults: {
              turnOrchestration: "broken" as never,
            },
            list: [
              {
                id: "main",
                turnOrchestration: "broken" as never,
              },
            ],
          },
        },
        agentId: "main",
      }),
    ).toBe("legacy");
  });
});
