// The one place the dashboard talks HTTP. The generated client (src/generated, from openapi.json)
// calls this for every operation; a refusal comes back as an Error with the server's message.

export const USER_HEADER = "X-CMBS-User";

/** Fetch a URL and return its JSON body. A refusal throws the server's words when it sends them
 *  as JSON (`{"detail": "..."}`), otherwise the HTTP status; a request that never reached the
 *  server says so. */
export async function http<T>(url: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch {
    throw new Error("the server could not be reached");
  }
  if (!response.ok) {
    throw new Error(await refusal(response));
  }
  return (await response.json()) as T;
}

/** What a refusal says: its JSON `detail` when that is text, else the HTTP status. */
async function refusal(response: Response): Promise<string> {
  const status = `the server answered HTTP ${response.status}`;
  try {
    const detail = ((await response.json()) as { detail?: unknown } | null)?.detail;
    return typeof detail === "string" ? detail : status;
  } catch {
    return status;
  }
}

/** The request options that make a call as a user. */
export function asUser(user: string): RequestInit {
  return { headers: { [USER_HEADER]: user } };
}
