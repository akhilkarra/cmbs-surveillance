import type {
  Check,
  CommentSection,
  CommentValue,
  FilingSource,
  Financials,
  LoanPageData,
  TapeValue,
} from "./api";
import { loanRead } from "./api";
import { formatValue, NOT_REPORTED } from "./format";
import { HistoryChart } from "./HistoryChart";
import { Loadable } from "./Loadable";
import { bookHref } from "./route";
import { useApi } from "./useApi";

function Checks({ checks }: { checks: Check[] }) {
  return (
    <ul className="checks">
      {checks.map((check) => (
        <li key={check.label} data-testid="check" data-outcome={check.outcome}>
          {check.label}
        </li>
      ))}
    </ul>
  );
}

function FilingLink({ source, testId }: { source: FilingSource; testId: string }) {
  return (
    <a href={source.url} target="_blank" rel="noreferrer" data-testid={testId}>
      {source.form} {source.accession} on EDGAR
    </a>
  );
}

function TapeRow({ value, loaded }: { value: TapeValue; loaded: string }) {
  return (
    <tr data-field={value.field}>
      <th scope="row">{value.label}</th>
      <td className="figure" data-testid="value" data-reported={value.value !== null}>
        {formatValue(value.kind, value.value)}
      </td>
      <td>
        <Checks checks={value.checks} />
        <span className="run" data-testid="loaded">
          {loaded}
        </span>
      </td>
    </tr>
  );
}

/** The financials' end and, in the API's words, how old they were (or why there is no age). */
export function FinancialsBlock({ financials }: { financials: Financials }) {
  return (
    <div className="financials" data-testid="financials">
      <h3>Most recent financials</h3>
      <p>
        Through <span data-testid="financials-end">{financials.end ?? NOT_REPORTED}</span>:{" "}
        <span data-testid="financials-age">{financials.status}</span>
      </p>
    </div>
  );
}

function CommentRow({ value }: { value: CommentValue }) {
  return (
    <tr data-field={value.field} data-status={value.status}>
      <th scope="row">{value.label}</th>
      <td>
        {value.value === null ? (
          <span className="no-value" data-testid="status">
            {value.status}
          </span>
        ) : (
          <span className="figure" data-testid="value">
            {value.value}
          </span>
        )}
        {value.review_reason && <span className="reason">{value.review_reason}</span>}
        {value.decision && (
          <span className="decision" data-testid="decision">
            {value.decision}
          </span>
        )}
      </td>
      <td>
        {value.sentence !== null && (
          <figure className="evidence">
            <figcaption data-testid="sentence-number">sentence {value.sentence}</figcaption>
            <blockquote data-testid="evidence">{value.evidence}</blockquote>
          </figure>
        )}
      </td>
      <td>
        <Checks checks={value.checks} />
      </td>
    </tr>
  );
}

export function CommentBlock({ comment }: { comment: CommentSection }) {
  return (
    <>
      <p className="provenance">
        <FilingLink source={comment.source} testId="comment-filing" />, filed{" "}
        {comment.source.filing_date}. Read by {comment.model} with prompt version{" "}
        {comment.prompt_version}, settings version {comment.settings_version};{" "}
        <span data-testid="comment-loaded">{comment.loaded.text}</span>.
      </p>
      <div className="table-scroll">
        <table className="values" data-testid="comment-values">
          <thead>
            <tr>
              <th scope="col">Field</th>
              <th scope="col">Value</th>
              <th scope="col">Sentence behind it</th>
              <th scope="col">Verified by</th>
            </tr>
          </thead>
          <tbody>
            {comment.values.map((value) => (
              <CommentRow key={value.field} value={value} />
            ))}
          </tbody>
        </table>
      </div>
      <h3>The comment as filed</h3>
      <ol className="sentences">
        {comment.sentences.map((sentence, index) => (
          // Sentences are numbered by position; the same words may appear twice.
          // biome-ignore lint/suspicious/noArrayIndexKey: position is the sentence's identity
          <li key={index}>{sentence}</li>
        ))}
      </ol>
    </>
  );
}

export function LoanContent({ data }: { data: LoanPageData }) {
  return (
    <>
      <header className="page-head">
        <a className="back" href={bookHref(data.month.key)}>
          Open alerts on the book, {data.month.label}
        </a>
        <h1 data-testid="loan-title">
          Loan {data.loan} of {data.deal}
        </h1>
        <p className="lede">
          {data.property_name ?? "Property not named"}. {data.month.label}, as the filings stood on{" "}
          <span data-testid="as-of">{data.as_of}</span>.
        </p>
      </header>
      <section aria-labelledby="history-heading">
        <h2 id="history-heading">History</h2>
        <HistoryChart history={data.history} asOf={data.as_of} />
      </section>
      <div className="columns">
        <section aria-labelledby="tape-heading">
          <h2 id="tape-heading">Loan tape</h2>
          <p className="provenance">
            <FilingLink source={data.tape.source} testId="tape-filing" />, filed{" "}
            {data.tape.source.filing_date}.
          </p>
          <div className="table-scroll">
            <table className="values" data-testid="tape-values">
              <thead>
                <tr>
                  <th scope="col">Field</th>
                  <th scope="col">Value</th>
                  <th scope="col">Verified by</th>
                </tr>
              </thead>
              <tbody>
                {data.tape.values.map((value) => (
                  <TapeRow key={value.field} value={value} loaded={data.tape.loaded.text} />
                ))}
              </tbody>
            </table>
          </div>
          <FinancialsBlock financials={data.financials} />
        </section>
        <section aria-labelledby="comment-heading">
          <h2 id="comment-heading">
            Servicer comment, <span data-testid="comment-month">{data.month.label}</span>
          </h2>
          {data.comment === null ? (
            <p className="empty">{data.comment_note}</p>
          ) : (
            <CommentBlock comment={data.comment} />
          )}
        </section>
      </div>
      <section aria-labelledby="flags-heading">
        <h2 id="flags-heading">Check flags</h2>
        {data.flags.length === 0 ? (
          <p className="empty">No check flagged this loan for {data.month.label}.</p>
        ) : (
          <ul className="flags" data-testid="flags">
            {data.flags.map((flag) => (
              <li key={flag.id}>
                <strong>{flag.check}</strong> {flag.message}
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-labelledby="alerts-heading">
        <h2 id="alerts-heading">Alerts this month</h2>
        {data.alerts.length === 0 ? (
          <p className="empty">No alerts on this loan for {data.month.label}.</p>
        ) : (
          <ul className="alerts" data-testid="loan-alerts">
            {data.alerts.map((alert) => (
              <li key={alert.alert_id}>
                <span className={`severity severity-${alert.severity}`}>{alert.severity}</span>{" "}
                {alert.message} <span className="state">{alert.state_text}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

export function LoanPage(props: {
  user: string;
  deal: string;
  loan: string;
  month: string | null;
  asOf: string | null;
}) {
  const resource = useApi(loanRead(props.deal, props.loan, props.month, props.asOf), props.user);
  return (
    <Loadable testId="loan" resource={resource} loading={`Reading loan ${props.loan}`}>
      {(data) => <LoanContent data={data} />}
    </Loadable>
  );
}
