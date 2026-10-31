import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["src/test-setup.ts"],
    // The grid test reads the page's colour tokens from styles.css.
    css: { include: [/styles\.css/] },
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      // src/generated is orval's client (openapi.json): generated, not written, so not measured;
      // the reads the dashboard makes through it are tested in api.test.ts.
      exclude: ["src/**/*.test.{ts,tsx}", "src/main.tsx", "src/test-*.ts", "src/generated/**"],
      // Floors only rise; set from the score on 2026-10-29, with the dashboard's tests.
      thresholds: { statements: 100, branches: 100, functions: 100, lines: 100 },
    },
  },
});
