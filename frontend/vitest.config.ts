import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    // Only unit tests under tests/. e2e/*.spec.ts is Playwright's (separate runner,
    // separate deps) — vitest must not try to import @playwright/test.
    include: ["tests/**/*.test.ts"],
    coverage: { provider: "v8", reporter: ["text", "lcov"] },
  },
  resolve: { alias: { "@": path.resolve(__dirname, ".") } },
});
