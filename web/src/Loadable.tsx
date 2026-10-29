import type { ReactNode } from "react";
import type { Resource } from "./useApi";

/** A page section that says when it is loading or failed, and shows its content when ready.
 *  `data-state` tells tests when the page is ready to read. */
export function Loadable<T>(props: {
  testId: string;
  resource: Resource<T>;
  loading: string;
  children: (data: T) => ReactNode;
}) {
  const { resource } = props;
  return (
    <main className="page" data-testid={props.testId} data-state={resource.state}>
      {resource.state === "loading" && <p className="status">{props.loading}</p>}
      {resource.state === "error" && (
        <p className="status failed" role="alert">
          {resource.message}
        </p>
      )}
      {resource.state === "ready" && props.children(resource.data)}
    </main>
  );
}
