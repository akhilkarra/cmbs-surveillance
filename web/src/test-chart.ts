// A stand-in for lightweight-charts, which draws on a canvas jsdom does not have. It records what
// the page asked the chart to draw, so tests read the series, markers and options from `charts`.
// It also stands in for the browser's colour scheme query (window.matchMedia), which jsdom lacks.

export interface FakeSeries {
  definition: unknown;
  options: Record<string, unknown>;
  pane: number | undefined;
  data: unknown[];
  markers: unknown[];
  /** The options last applied to the series' price scale. */
  scale: unknown;
  setData(data: unknown[]): void;
  priceScale(): { applyOptions(options: unknown): void };
}

export interface FakeChart {
  element: HTMLElement;
  options: Record<string, unknown>;
  series: FakeSeries[];
  hover: ((param: { time?: unknown }) => void) | null;
  fitted: boolean;
  removed: boolean;
}

export const charts: FakeChart[] = [];

export const ColorType = { Solid: "solid", VerticalGradient: "gradient" };
export const LineSeries = { type: "Line" };

export function createChart(element: HTMLElement, options: Record<string, unknown>) {
  const chart: FakeChart = {
    element,
    options,
    series: [],
    hover: null,
    fitted: false,
    removed: false,
  };
  charts.push(chart);
  return {
    addSeries(definition: unknown, seriesOptions: Record<string, unknown>, pane?: number) {
      const series: FakeSeries = {
        definition,
        options: seriesOptions,
        pane,
        data: [],
        markers: [],
        scale: undefined,
        setData(data) {
          series.data = data;
        },
        priceScale() {
          return {
            applyOptions(scale) {
              series.scale = scale;
            },
          };
        },
      };
      chart.series.push(series);
      return series;
    },
    subscribeCrosshairMove(handler: (param: { time?: unknown }) => void) {
      chart.hover = handler;
    },
    timeScale() {
      return {
        fitContent() {
          chart.fitted = true;
        },
      };
    },
    remove() {
      chart.removed = true;
    },
  };
}

export function createSeriesMarkers(series: FakeSeries, markers: unknown[]) {
  series.markers = markers;
}

/** The system's colour scheme: the queries the page asked about and the listeners it keeps. */
export const colorScheme = {
  queries: [] as string[],
  listeners: new Set<() => void>(),
  change() {
    for (const listener of [...colorScheme.listeners]) listener();
  },
};

export function matchMedia(query: string) {
  colorScheme.queries.push(query);
  return {
    matches: false,
    media: query,
    addEventListener(type: string, listener: () => void) {
      if (type === "change") colorScheme.listeners.add(listener);
    },
    removeEventListener(type: string, listener: () => void) {
      if (type === "change") colorScheme.listeners.delete(listener);
    },
  };
}
