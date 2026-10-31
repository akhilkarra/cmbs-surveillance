import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { http } from "./http";
import { useApi } from "./useApi";

afterEach(() => {
  vi.unstubAllGlobals();
});

function Probe({ path, user }: { path: string; user: string }) {
  // A new read object on every render, as a caller that does not memoise it makes.
  const read = { key: path, send: (init: RequestInit) => http<{ name: string }>(path, init) };
  const resource = useApi(read, user);
  return (
    <p data-testid="probe">
      {resource.state === "ready" ? resource.data.name : resource.state}
      {resource.state === "error" ? `: ${resource.message}` : ""}
    </p>
  );
}

/** A fetch whose answers the test releases one at a time, in any order. */
function held() {
  const waiting = new Map<string, (response: Response) => void>();
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (path: string, init: RequestInit) =>
        new Promise<Response>((resolve) => {
          const user = (init.headers as Record<string, string>)["X-CMBS-User"];
          waiting.set(`${user} ${path}`, resolve);
        }),
    ),
  );
  return (key: string, body: unknown, status = 200) => {
    waiting.get(key)?.(new Response(JSON.stringify(body), { status }));
  };
}

describe("useApi", () => {
  it("reads once while the key and the user stay the same, however often the page draws", async () => {
    const release = held();
    const { rerender } = render(<Probe path="/a" user="pat" />);
    rerender(<Probe path="/a" user="pat" />);
    release("pat /a", { name: "a" });
    await waitFor(() => expect(screen.getByTestId("probe").textContent).toBe("a"));
    rerender(<Probe path="/a" user="pat" />);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("probe").textContent).toBe("a");
  });

  it("drops an answer for a path no longer shown, and shows the current one", async () => {
    const release = held();
    const { rerender } = render(<Probe path="/a" user="pat" />);
    rerender(<Probe path="/b" user="pat" />);
    release("pat /b", { name: "b" });
    await waitFor(() => expect(screen.getByTestId("probe").textContent).toBe("b"));
    release("pat /a", { name: "a" });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.getByTestId("probe").textContent).toBe("b");
  });

  it("asks again when the user changes, loading meanwhile", async () => {
    const release = held();
    const { rerender } = render(<Probe path="/a" user="pat" />);
    release("pat /a", { name: "pat's" });
    await waitFor(() => expect(screen.getByTestId("probe").textContent).toBe("pat's"));
    rerender(<Probe path="/a" user="alex" />);
    expect(screen.getByTestId("probe").textContent).toBe("loading");
    release("alex /a", { name: "alex's" });
    await waitFor(() => expect(screen.getByTestId("probe").textContent).toBe("alex's"));
  });

  it("drops a failure for a path no longer shown", async () => {
    const release = held();
    const { rerender } = render(<Probe path="/a" user="pat" />);
    rerender(<Probe path="/b" user="pat" />);
    release("pat /a", { detail: "refused" }, 403);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.getByTestId("probe").textContent).toBe("loading");
    release("pat /b", { detail: "no such loan" }, 422);
    await waitFor(() =>
      expect(screen.getByTestId("probe").textContent).toBe("error: no such loan"),
    );
  });
});
