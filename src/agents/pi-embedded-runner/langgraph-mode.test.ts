import { describe, expect, it } from "vitest";

describe("resolveTurnOrchestrationMode", () => {
  it("always returns langgraph", async () => {
    const { resolveTurnOrchestrationMode } = await import("./langgraph-mode.js");
    expect(resolveTurnOrchestrationMode()).toBe("langgraph");
  });
});
