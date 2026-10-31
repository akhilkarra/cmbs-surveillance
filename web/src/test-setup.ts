// Testing Library unmounts what a test rendered after each test.
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { charts, colorScheme, matchMedia } from "./test-chart";

// jsdom has no canvas: every test draws charts on the stand-in (src/test-chart.ts), and asks the
// stand-in's colour scheme, as jsdom has no window.matchMedia either.
vi.mock("lightweight-charts", () => import("./test-chart"));
window.matchMedia = matchMedia as unknown as typeof window.matchMedia;

afterEach(() => {
  cleanup();
  charts.length = 0;
  colorScheme.queries.length = 0;
  colorScheme.listeners.clear();
});
