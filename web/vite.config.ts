import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    include: ["src/**/*.test.ts"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.ts", "src/main.tsx"],
      // Floors only rise; set from the score on 2026-10-28.
      thresholds: { statements: 85, branches: 100, functions: 75, lines: 85 },
    },
  },
});
