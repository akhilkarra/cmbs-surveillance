// The one place the dashboard talks HTTP. The generated client (src/generated, from openapi.json)
// calls this for every operation; a refusal comes back as an Error with the server's message.

export const USER_HEADER = "X-CMBS-User";

/** Fetch a URL and return its JSON body; any answer other than 2xx throws the server's words. */
export async function http<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, init);
  const body: unknown = await response.json();
  if (!response.ok) {
    const detail = (body as { detail?: unknown }).detail;
    throw new Error(
      typeof detail === "string" ? detail : `the server answered HTTP ${response.status}`,
    );
  }
  return body as T;
}

/** The request options that make a call as a user. */
export function asUser(user: string): RequestInit {
  return { headers: { [USER_HEADER]: user } };
}
