import { useEffect, useState } from "react";
import type { ApiRead } from "./api";
import { asUser } from "./http";

export type Resource<T> =
  | { state: "loading" }
  | { state: "ready"; data: T }
  | { state: "error"; message: string };

/** Make a read as a user, again whenever either changes. An answer that arrives after the read
 *  changed, or after the page closed, is dropped. Pass a read that keeps its identity while its
 *  key does (`useMemo`), or it is made again on every render. */
export function useApi<T>(read: ApiRead<T>, user: string): Resource<T> {
  const [resource, setResource] = useState<Resource<T>>({ state: "loading" });
  useEffect(() => {
    let current = true;
    setResource({ state: "loading" });
    read.send(asUser(user)).then(
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
  }, [read, user]);
  return resource;
}
