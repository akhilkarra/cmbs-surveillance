import { useEffect, useRef, useState } from "react";
import type { ApiRead } from "./api";
import { asUser } from "./http";

export type Resource<T> =
  | { state: "loading" }
  | { state: "ready"; data: T }
  | { state: "error"; message: string };

/** Make a read as a user, again whenever its key (its URL) or the user changes, and only then:
 *  a new read object with the same key is the same read. An answer that arrives after the read
 *  changed, or after the page closed, is dropped. */
export function useApi<T>(read: ApiRead<T>, user: string): Resource<T> {
  const [resource, setResource] = useState<Resource<T>>({ state: "loading" });
  // The latest read's `send`, so the effect below can follow the key alone.
  const latest = useRef(read);
  latest.current = read;
  const { key } = read;
  // biome-ignore lint/correctness/useExhaustiveDependencies: the key names the read; `send` comes from the ref, so a read rebuilt on every render is not sent again.
  useEffect(() => {
    let current = true;
    setResource({ state: "loading" });
    latest.current.send(asUser(user)).then(
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
  }, [key, user]);
  return resource;
}
