// A stand-in for lightweight-charts, which draws on a canvas jsdom does not have. It records what
// the page asked the chart to draw, so tests read the series, markers and options from `charts`.

export interface FakeSeries {
  definition: unknown;
  options: Record<string, unknown>;
  pane: number | undefined;
  data: unknown[];
  markers: unknown[];
  setData(data: unknown[]): void;
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
        setData(data) {
          series.data = data;
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
