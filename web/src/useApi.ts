import { useEffect, useState } from "react";
import { getJson } from "./api";

export type Resource<T> =
  | { state: "loading" }
  | { state: "ready"; data: T }
  | { state: "error"; message: string };

/** Read an API path as a user, again whenever either changes. An answer that arrives after the
 *  path changed, or after the page closed, is dropped. */
export function useApi<T>(path: string, user: string): Resource<T> {
  const [resource, setResource] = useState<Resource<T>>({ state: "loading" });
  useEffect(() => {
    let current = true;
    setResource({ state: "loading" });
    getJson<T>(path, user).then(
      (data) => {
        if (current) setResource({ state: "ready", data });
      },
      (error: Error) => {
        if (current) setResource({ state: "error", message: error.message });
      },
    );
    return () => {
      current = false;
    };
  }, [path, user]);
  return resource;
}
