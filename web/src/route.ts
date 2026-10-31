// Pages live in the URL's hash, so any static server can serve the bundle:
//   #/book            the latest month's book view
//   #/book/2026-09    a month's book view
//   #/loan/<deal>/<loan>[/2026-09]   one loan, for the deal's latest month or the one named
//   #/loan/<deal>/<loan>?as_of=2026-09-01   the loan as the filings stood on a day (today if not)
// The user reading the page is `?user=<name>` (the demo names users; single sign-on comes later).

export type Route =
  | { page: "book"; month: string | null }
  | { page: "loan"; deal: string; loan: string; month: string | null; asOf: string | null }
  | { page: "missing"; hash: string };

function decoded(hash: string): string[] | null {
  try {
    return hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  } catch {
    return null; // a malformed escape such as "%E0%A4%A"
  }
}

export function parseRoute(hash: string): Route {
  const start = hash.indexOf("?");
  const path = start === -1 ? hash : hash.slice(0, start);
  const query = new URLSearchParams(start === -1 ? "" : hash.slice(start + 1));
  const parts = decoded(path);
  if (parts === null) {
    return { page: "missing", hash };
  }
  const [page, ...rest] = parts;
  if ((page === "" || page === "book") && rest.length <= 1) {
    return { page: "book", month: rest[0] ?? null };
  }
  const [deal, loan, month] = rest;
  if (page === "loan" && deal && loan && rest.length <= 3) {
    return { page: "loan", deal, loan, month: month ?? null, asOf: query.get("as_of") || null };
  }
  return { page: "missing", hash };
}

export function bookHref(month: string): string {
  return `#/book/${month}`;
}

export function loanHref(deal: string, loan: string, month: string): string {
  return `#/loan/${encodeURIComponent(deal)}/${encodeURIComponent(loan)}/${month}`;
}

export function userFrom(search: string): string | null {
  const user = new URLSearchParams(search).get("user");
  return user?.trim() ? user.trim() : null;
}
