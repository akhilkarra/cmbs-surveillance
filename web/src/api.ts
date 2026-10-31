// The dashboard's reads of the API (src/cmbs/dashboard/routes.py), through the client orval
// generates from the committed schema (src/generated, from openapi.json). Money and ratios
// arrive as decimal strings, dates as ISO strings; every label and ordering is the server's.

import type { BookPage, LoanPage } from "./generated/api.schemas";
import {
  getDashboardBook,
  getDashboardLoan,
  getGetDashboardBookUrl,
  getGetDashboardLoanUrl,
} from "./generated/dashboard/dashboard";

// The API's types under the names the pages use; the components are BookPage and LoanPage.
export type {
  BookPage as BookPageData,
  BookRow,
  Check,
  CommentSection,
  CommentValue,
  FilingSource,
  Financials,
  LoanPage as LoanPageData,
  TapeValue,
} from "./generated/api.schemas";

/** One read a page makes: `key` is its URL, which names it; `send` makes it with the request
 *  options given (the user's header). */
export interface ApiRead<T> {
  key: string;
  send: (init: RequestInit) => Promise<T>;
}

/** The book for a month, or for the latest month built when none is named. */
export function bookRead(month: string | null): ApiRead<BookPage> {
  const params = month === null ? {} : { month };
  return {
    key: getGetDashboardBookUrl(params),
    send: (init) => getDashboardBook(params, init),
  };
}

/** A deal's loan for a month, or for the deal's latest month when none is named. */
export function loanRead(deal: string, loan: string, month: string | null): ApiRead<LoanPage> {
  const params = month === null ? { deal, loan } : { deal, loan, month };
  return {
    key: getGetDashboardLoanUrl(params),
    send: (init) => getDashboardLoan(params, init),
  };
}
