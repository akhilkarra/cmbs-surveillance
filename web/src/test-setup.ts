// Testing Library unmounts what a test rendered after each test.
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { charts } from "./test-chart";

// jsdom has no canvas: every test draws charts on the stand-in (src/test-chart.ts).
vi.mock("lightweight-charts", () => import("./test-chart"));

afterEach(() => {
  cleanup();
  charts.length = 0;
});
