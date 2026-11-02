"""Lead-lag study, version 2: the analysis fixed in PLAN-v2.md, run once.

Question: once a CMBS loan is in special servicing, does the servicer's 10-D comment tell you
something the loan tape does not yet show?

Sections follow the plan: data, cleaning, episodes, outcomes, Analysis A (lead-lag),
Analysis B (prediction at landmarks), Analysis C (descriptive), then the two sanity checks.
Every judgment the plan leaves open is fixed in the constants below, before any result is read,
and listed in DEVIATIONS.

Usage: python analyze_v2.py            -> results_v2.json, results_v2.md, handcheck_v2.txt
"""

import json
import math
import os
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import numpy as np
import psycopg

HERE = Path(__file__).resolve().parent
DSN = "postgresql://cmbs:cmbs@localhost:5432/cmbs_study"
SEED = 20261102
N_BOOT = 2000
JEV_MODEL, JEV_PROMPT, JEV_SETTINGS = "jev-1.14.0", 3, 9
LANDMARKS = (0, 3, 6)
OPEN_FOLLOWUP_MONTHS = 24
LOSS_THRESHOLD = 0.02  # realized-loss rise as a share of the balance at transfer
C_GRID = (0.01, 0.1, 1.0)

# ---------------------------------------------------------------------------------------------
# Patterns, all stated up front
# ---------------------------------------------------------------------------------------------

# A comment that only says comments are unavailable (the whole text, after the report's legend
# footnote, a leading date or loan number, and trailing punctuation are stripped).
PLACEHOLDER = re.compile(
    r"^(?:please refer to servicer reports (?:for comments )?as they are too lengthy to include"
    r" for this(?: cycle)?"
    r"|(?:special servic(?:er|ing) |ss )comments (?:are |were )?not available(?: for this cycle)?"
    r"|(?:th\w* (?:was|were|are) )?no comments? (?:provided(?: for this loan)?|to report this cycle"
    r"|this cycle))$",
    re.I,
)
LEGEND = re.compile(r"\(\d\)\s*(?:Resolution Strategy Code|Property Type Code).*$", re.I)
LEADING_TAG = re.compile(r"^(?:\d{1,2}/\d{1,2}/\d{2,4}|\d{6,})\s*[-:]?\s*")

# Analysis A text families (keywords). Liquidation: the plan's six terms and their spellings.
LIQUIDATION = re.compile(
    r"foreclos|receiver|\bREO\b|real estate owned|deed[- ]in[- ]lieu|note sale|sale of the note"
    r"|sell the (?:note|loan)|discounted pay ?off|\bDPO\b",
    re.I,
)
RETURN = re.compile(
    r"return(?:ed|s|ing)? (?:\w+ )?to (?:the )?(?:master|MS\b)|transferred back to (?:the )?master"
    r"|corrected (?:mortgage )?loan|monitored for return|pending return",
    re.I,
)
WORKOUT_DECIDED_TERMS = re.compile(r"modif|\bextension|\bextend", re.I)
REQUEST = re.compile(r"\brequest|\bpropos|\bseek|\bsought\b|\basked\b|\basking\b", re.I)
WORKOUT_REQUEST_TERMS = re.compile(
    r"modif|\bextension|\bextend|forbear|\brelief|\bdefer|restructur|discounted pay ?off|\bDPO\b"
    r"|assum|\brelease",
    re.I,
)
SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")

# Analysis B keyword families beyond those above (v1's patterns, unchanged).
OTHER_FAMILIES = {
    "bankruptcy": re.compile(r"bankrupt|chapter 11", re.I),
    "covid": re.compile(r"covid|pandemic", re.I),
    "cash_trap": re.compile(r"cash management|lockbox|cash trap|sweep", re.I),
    "vacancy": re.compile(r"vacan|dark\b|tenant (?:vacated|left)|lease expir", re.I),
    "litigation": re.compile(
        r"litigat|lawsuit|complaint|default letter|notice of default|\bROR\b"
        r"|reservation of rights|pre-?negotiation|\bPNA\b",
        re.I,
    ),
}

# Keyword precision: the plan's five patterns. "may" is matched lower-case only, so the month
# "May" is not counted as a hedge.
HEDGES = {
    "to avoid": re.compile(r"to avoid", re.I),
    "moratorium": re.compile(r"moratorium", re.I),
    "if necessary": re.compile(r"if necessary", re.I),
    "potential": re.compile(r"potential", re.I),
    "may": re.compile(r"\bmay\b"),
}

# Schedule AL workout-strategy codes.
TAPE_LIQUIDATION = {"2", "5", "6", "7", "10"}
TAPE_LIQUIDATION_EXIT = {"5", "6", "7", "10"}
TAPE_WORKOUT = {"1", "4"}
TAPE_RETURN = {"8", "9"}

JEV_LIQUIDATION = {
    "foreclosure",
    "property in REO",
    "deed in lieu of foreclosure",
    "note sale",
    "discounted payoff",
}
JEV_RETURN = {"pending return to master servicer", "resolved"}
JEV_WORKOUT = {"modification", "extension"}
JEV_TRANSFER_REASONS = (
    "monetary default",
    "imminent monetary default",
    "maturity default",
    "imminent maturity default",
    "non-monetary default",
    "bankruptcy",
    "other",
)

PAY_RANK = {"0": 0, "A": 1, "B": 2, "1": 3, "2": 4, "3": 5, "4": 3, "5": 6}
PROPERTY_TYPES = ("RT", "OF", "LO", "MF", "MU", "SS", "IN", "CH", "MH", "SE", "WH", "HC", "98")

FAMILIES = ("liquidation", "return", "workout_decided", "borrower_request")

DEVIATIONS = [
    "Placeholder pattern: a comment is dropped only when its whole text (after removing the "
    "report's '(1) Resolution Strategy Code' legend, a leading date or loan number, and trailing "
    "punctuation) is a 'too lengthy' / 'not available' / 'no comment provided' notice. "
    "'Special Servicer comments were not provided, as this loan returned to Master Servicing' is "
    "kept, because it states a fact (the return); cross-references ('refer to commentary on "
    "loan N') are kept, as the plan names only unavailable-comment notices.",
    "Wrong-deal months: only the tape rows are dropped; the 10-D comments for those months are "
    "kept, because M-029 concerns the ABS-EE tape, not the distribution report. Loans present = "
    "loans with a property name that month.",
    "A-note join: a single trailing letter is stripped, and only when the stripped ID is on the "
    "deal's tape and the original ID is not (judged over the deal's whole tape). Comments of "
    "several notes of one loan in one month are joined, identical texts once.",
    "Exit from the tape: after liquidation the tape keeps most loans with a zero balance and the "
    "loss carried forward, so 'exits the tape' is read as the first month with an ending balance "
    "of zero, or, for a loan that disappears while the deal keeps reporting, the month after its "
    "last row.",
    "Liquidation exit: the loan's latest non-blank workout code at or before its exit row is 5, "
    "6, 7 or 10. (The count under the alternative reading, any such code in the window before the "
    "exit, is reported, not used.)",
    "Loss by realized loss: a month's realized loss minus the last reported value before it "
    "(zero if none) is at least 2% of the balance at transfer. Balance at transfer = ending "
    "balance in the episode's first month, else the latest positive earlier ending balance, else "
    "that month's beginning balance.",
    "Cured or returned: read as a return date on or after the transfer date, or workout code 8 or "
    "9, anywhere in the window, with no loss; 'followed by the return' is read as description. "
    "The episodes labelled by code 8 or 9 alone are counted.",
    "Loss takes precedence over a return in the same window (the plan's 'with no loss').",
    "Found by the hand check and fixed before Analyses A-C were first run: return evidence (a "
    "return date or code 8/9) counts only up to the exit row, because the tape keeps paid-off "
    "loans as zero-balance carry rows that can show code 8 after the payoff.",
    "Resolution date for landmark eligibility = the earliest of the loss month, the cure/return "
    "month and the exit month, so a loan cured before the landmark is out of that landmark's set "
    "even if it later takes a loss.",
    "Open episodes: the plan's '24 months' is read as follow-up from the episode's first month to "
    "its window end. Primary: every open episode is dropped (none has a label); the counts "
    "with >= 24 and < 24 months are reported. Sensitivity: open episodes with >= 24 months are "
    "'not loss'; those with < 24 stay dropped.",
    "Workout decided, tape side: a modification code counts only when it is new in the window "
    "(blank/0 or different from the last code reported before the episode), so a code carried "
    "from an earlier modification is not read as this episode's decision.",
    "Workout keywords: a sentence with modif/extension/extend counts as decided only if it has no "
    "request word (request, propos, seek, sought, asked, asking). Borrower request: a sentence "
    "with a request word and a workout term. The same borrower-request family is the "
    "'workout-request' feature in Analysis B.",
    "Jev families: liquidation = foreclosure, property in REO, deed in lieu, note sale, "
    "discounted payoff; return = pending return to master servicer, resolved; workout decided = "
    "modification, extension; borrower request = any auto-accepted borrower_request value. When "
    "several notes of one loan have answers in one month, each family is set if any note's "
    "answer is in it.",
    "Analysis B features: a missing indicator is added for months since financials end (needed "
    "to impute it); payment-status rank uses v1's map with blank as current; property-type "
    "dummies cover every code on the tape (blank is the base); log length is of all comments up "
    "to the landmark; the tape row used is the landmark month's, else the latest before it in "
    "the window. Features are standardised inside each fit; inner CV scores by AUC.",
    "Analysis C: 'tape has not coded liquidation' = no workout code 2, 5, 6, 7 or 10 in any "
    "window month up to the landmark; 'text names liquidation' = the liquidation keyword family "
    "in any comment up to the landmark. The Jev split is added as a labelled extra.",
]


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def code(v):
    if v is None:
        return None
    s = str(v).strip().upper()
    if not s:
        return None
    return (s.lstrip("0") or "0") if s.isdigit() else s


def norm_name(name):
    return re.sub(r"[^A-Z0-9]", "", (name or "").upper())


def sentences(text):
    return SENTENCE_END.split(text)


def wilson(k, n, z=1.959964):
    if n == 0:
        return [None, None]
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [centre - half, centre + half]


def auc(y, s):
    from sklearn.metrics import roc_auc_score

    return float(roc_auc_score(y, s))


# ---------------------------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------------------------

TAPE_COLS = (
    "deal",
    "loan",
    "period",
    "transfer",
    "msr",
    "ws",
    "ps",
    "realized_loss",
    "ending_balance",
    "beginning_balance",
    "dscr",
    "occupancy",
    "financials_end",
    "mod_code",
    "property_type",
    "property_name",
)


def load():
    with psycopg.connect(DSN) as conn:
        tape = [
            dict(zip(TAPE_COLS, r))
            for r in conn.execute(
                """select d.name, lp.loan, lp.period, lp.special_servicer_transfer_date,
                          lp.master_servicer_return_date, lp.workout_strategy, lp.payment_status,
                          lp.realized_loss, lp.ending_balance, lp.beginning_balance, lp.dscr,
                          lp.occupancy, lp.financials_end, lp.modification_code,
                          lp.property_type, lp.property_name
                   from silver.loan_period lp join silver.deal d on d.id = lp.deal_id
                   order by d.name, lp.loan, lp.period"""
            ).fetchall()
        ]
        comments = conn.execute(
            """select d.name, c.loan_id, c.period, c.comment
               from silver.report_comment c join silver.deal d on d.id = c.deal_id
               order by d.name, c.period, c.loan_id"""
        ).fetchall()
        jev = conn.execute(
            """select d.name, e.loan_id, e.period, f.field, f.model_value
               from silver.extracted_field f join silver.extraction e on e.id = f.extraction_id
               join silver.deal d on d.id = e.deal_id
               where e.model = %s and e.prompt_version = %s and e.settings_version = %s
                 and f.outcome = 'auto-accept' and f.model_value is not null""",
            (JEV_MODEL, JEV_PROMPT, JEV_SETTINGS),
        ).fetchall()
        jev_extractions = conn.execute(
            "select count(*) from silver.extraction where model = %s and prompt_version = %s "
            "and settings_version = %s",
            (JEV_MODEL, JEV_PROMPT, JEV_SETTINGS),
        ).fetchone()[0]
        run_months = dict(
            conn.execute("select outcome, count(*) from app.run_month group by 1").fetchall()
        )
    return tape, comments, jev, jev_extractions, run_months


def describe_data(tape, comments, jev_extractions, run_months):
    deals = sorted({r["deal"] for r in tape})
    failed = []
    log = HERE / "backfill.jsonl"
    if log.exists():
        for line in log.read_text().splitlines():
            row = json.loads(line)
            if row.get("errors") and not row.get("loaded"):
                err = row["errors"][0].strip()
                kind = (
                    "shared filing already registered (ConflictingFiling)"
                    if "ConflictingFiling" in err
                    else "EDGAR search error (KeyError 'hits')"
                    if "KeyError" in err
                    else err
                )
                failed.append({"deal": row["deal"], "reason": kind, "error": err})
    vintages = Counter(re.search(r"20(1[789])", d).group(0) for d in deals)
    return {
        "deals_loaded": len(deals),
        "vintages": dict(sorted(vintages.items())),
        "tape_periods": [str(min(r["period"] for r in tape)), str(max(r["period"] for r in tape))],
        "comment_periods": [str(min(c[2] for c in comments)), str(max(c[2] for c in comments))],
        "loan_months": len(tape),
        "comments": len(comments),
        "jev_extractions": jev_extractions,
        "run_months_by_outcome": run_months,
        "deals_failed": len(failed),
        "deals_failed_by_reason": dict(Counter(f["reason"] for f in failed)),
        "deals_failed_list": failed,
    }


# ---------------------------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------------------------


def clean_wrong_deal_tapes(tape):
    """Drop deal-months where > half the loans present differ from their modal property name."""
    names = defaultdict(dict)  # (deal, period) -> loan -> name
    counts = defaultdict(lambda: defaultdict(Counter))
    for r in sorted(tape, key=lambda r: (r["deal"], r["period"], r["loan"])):
        if r["property_name"]:
            n = norm_name(r["property_name"])
            names[(r["deal"], r["period"])][r["loan"]] = n
            counts[r["deal"]][r["loan"]][n] += 1
    modal = {
        deal: {loan: c.most_common(1)[0][0] for loan, c in loans.items()}
        for deal, loans in counts.items()
    }
    dropped = set()
    for (deal, period), present in names.items():
        differ = sum(name != modal[deal][loan] for loan, name in present.items())
        if differ / len(present) > 0.5:
            dropped.add((deal, period))
    kept = [r for r in tape if (r["deal"], r["period"]) not in dropped]
    v1 = set()
    v1_path = HERE / "results.json"
    if v1_path.exists():
        for s in json.loads(v1_path.read_text())["deal_months_dropped_as_another_deals"]:
            d, p = s.rsplit(" ", 1)
            v1.add((d, date.fromisoformat(p)))
    first_month = {}
    for r in tape:
        first_month[r["deal"]] = min(first_month.get(r["deal"], r["period"]), r["period"])
    report = {
        "deal_months_dropped": len(dropped),
        "loan_months_dropped": len(tape) - len(kept),
        "deal_months_dropped_that_are_the_deals_first_month": sum(
            p == first_month[d] for d, p in dropped
        ),
        "deals_affected": len({d for d, _ in dropped}),
        "dropped_list": [f"{d} {p}" for d, p in sorted(dropped)],
        "new_vs_v1": [f"{d} {p}" for d, p in sorted(dropped - v1)],
        "v1_only": [f"{d} {p}" for d, p in sorted(v1 - dropped)],
    }
    return kept, report


def is_placeholder(text):
    t = re.sub(r"\s+", " ", text).strip()
    t = LEGEND.sub("", t).strip()
    t = LEADING_TAG.sub("", t).strip()
    t = re.sub(r"[\s.]+$", "", t)
    return bool(PLACEHOLDER.match(t))


def clean_comments(comments, jev, tape):
    """Drop placeholders, join A-note IDs, and attach Jev answers to the same keys."""
    tape_loans = defaultdict(set)
    for r in tape:
        tape_loans[r["deal"]].add(r["loan"])

    def join(deal, loan_id):
        if loan_id in tape_loans[deal]:
            return loan_id, "direct"
        m = re.fullmatch(r"(.+?)[A-Za-z]", loan_id)
        if m and m.group(1) in tape_loans[deal]:
            return m.group(1), "a_note"
        return None, "unjoined"

    texts = defaultdict(lambda: defaultdict(list))  # (deal, loan) -> period -> [text]
    kept_keys = {}  # (deal, comment loan_id, period) -> tape loan
    stats = Counter()
    unjoined_ids = Counter()
    for deal, loan_id, period, text in comments:
        stats["comments"] += 1
        if is_placeholder(text):
            stats["placeholder_dropped"] += 1
            continue
        loan, how = join(deal, loan_id)
        stats[f"joined_{how}"] += 1
        if loan is None:
            unjoined_ids[loan_id] += 1
            continue
        kept_keys[(deal, loan_id, period)] = loan
        if text not in texts[(deal, loan)][period]:
            texts[(deal, loan)][period].append(text)
        else:
            stats["duplicate_note_text_merged"] += 1
    comment_text = {k: {p: " ".join(v) for p, v in by.items()} for k, by in texts.items()}

    jev_by = defaultdict(lambda: defaultdict(set))  # (deal, loan, period) -> field -> values
    for deal, loan_id, period, field, value in jev:
        loan = kept_keys.get((deal, loan_id, period))
        if loan is not None:
            jev_by[(deal, loan, period)][field].add(value)

    report = {
        "comments_total": stats["comments"],
        "placeholder_comments_dropped": stats["placeholder_dropped"],
        "joined_directly": stats["joined_direct"],
        "joined_by_stripping_note_letter": stats["joined_a_note"],
        "still_unjoined": stats["joined_unjoined"],
        "still_unjoined_top_ids": unjoined_ids.most_common(10),
        "identical_note_texts_merged": stats["duplicate_note_text_merged"],
        "loan_months_with_comment": sum(len(v) for v in comment_text.values()),
        "loan_months_with_jev_answer": len(jev_by),
    }
    return comment_text, jev_by, report


# ---------------------------------------------------------------------------------------------
# Episodes and outcomes
# ---------------------------------------------------------------------------------------------


def index_tape(tape):
    by_loan = defaultdict(dict)
    deal_last = {}
    for r in tape:
        by_loan[(r["deal"], r["loan"])][r["period"]] = r
        deal_last[r["deal"]] = max(deal_last.get(r["deal"], r["period"]), r["period"])
    return by_loan, deal_last


def build_episodes(by_loan, deal_last, comment_text, jev_by, window_mode="fixed"):
    """One episode per transfer date. window_mode 'v1bug' reproduces v1's window for the check."""
    episodes = []
    for (deal, loan), rows in by_loan.items():
        periods = sorted(rows)
        first = {}
        for p in periods:
            t = rows[p]["transfer"]
            if t is not None and t not in first:
                first[t] = p
        if window_mode == "fixed":
            transfers = sorted(first, key=lambda t: (first[t], t))
        else:
            transfers = sorted(first)  # v1 ordered by the date itself
        texts = comment_text.get((deal, loan), {})
        for i, t in enumerate(transfers):
            start = first[t]
            nxt = transfers[i + 1] if i + 1 < len(transfers) else None
            if window_mode == "fixed":
                end = add_months(first[nxt], -1) if nxt is not None else deal_last[deal]
                window = [p for p in periods if start <= p <= end]
                com = {p: s for p, s in texts.items() if start <= p <= end}
            else:  # v1: months from start whose transfer date is not the next one; not contiguous
                window = [p for p in periods if p >= start and rows[p]["transfer"] != nxt]
                end = window[-1]
                com = {p: s for p, s in texts.items() if p >= start and p in set(window)}
            episodes.append(
                {
                    "deal": deal,
                    "loan": loan,
                    "transfer": t,
                    "start": start,
                    "end": end,
                    "window": window,
                    "rows": rows,
                    "periods": periods,
                    "comments": com,
                    "jev": {p: jev_by[(deal, loan, p)] for p in com if (deal, loan, p) in jev_by},
                    "deal_last": deal_last[deal],
                }
            )
    return episodes


def balance_at_transfer(ep):
    rows, start = ep["rows"], ep["start"]
    b = rows[start]["ending_balance"]
    if b is not None and b > 0:
        return float(b)
    for p in reversed([p for p in ep["periods"] if p < start]):
        b = rows[p]["ending_balance"]
        if b is not None and b > 0:
            return float(b)
    b = rows[start]["beginning_balance"]
    return float(b) if b is not None and b > 0 else None


def label_v2(ep):
    """The plan's outcome: loss, cured_returned, other_exit or open, with event months."""
    rows, window = ep["rows"], ep["window"]
    win = set(window)
    bal0 = balance_at_transfer(ep)
    threshold = LOSS_THRESHOLD * bal0 if bal0 else 0.0
    loss_rise, prev = None, 0.0
    for p in ep["periods"]:
        v = rows[p]["realized_loss"]
        if v is None:
            continue
        v = float(v)
        if p in win and loss_rise is None and v - prev > 0 and v - prev >= threshold:
            loss_rise = p
        prev = v
    zero = next(
        (
            p
            for p in window
            if rows[p]["ending_balance"] is not None and rows[p]["ending_balance"] == 0
        ),
        None,
    )
    last_row = ep["periods"][-1]
    gone = last_row < ep["deal_last"] and last_row in win
    exit_month = zero if zero is not None else (add_months(last_row, 1) if gone else None)
    exit_row = zero if zero is not None else (last_row if gone else None)
    loss_exit, any_code_exit = None, None
    if exit_row is not None:
        codes = [code(rows[p]["ws"]) for p in window if p <= exit_row and code(rows[p]["ws"])]
        if codes and codes[-1] in TAPE_LIQUIDATION_EXIT:
            loss_exit = exit_month
        if any(c in TAPE_LIQUIDATION_EXIT for c in codes):
            any_code_exit = exit_month
    # Return evidence counts only up to the exit row: a code 8 on a zero-balance carry row after a
    # payoff is not a return (found by the hand check, before Analyses A-C were run).
    live = [p for p in window if exit_row is None or p <= exit_row]
    ret = next(
        (
            p
            for p in live
            if (rows[p]["msr"] is not None and rows[p]["msr"] >= ep["transfer"])
            or code(rows[p]["ws"]) in TAPE_RETURN
        ),
        None,
    )
    ret_date = next(
        (p for p in live if rows[p]["msr"] is not None and rows[p]["msr"] >= ep["transfer"]), None
    )
    loss_months = [p for p in (loss_rise, loss_exit) if p is not None]
    loss_at = min(loss_months) if loss_months else None
    if loss_at is not None:
        outcome = "loss"
    elif ret is not None:
        outcome = "cured_returned"
    elif exit_month is not None:
        outcome = "other_exit"
    else:
        outcome = "open"
    events = [p for p in (loss_at, ret, exit_month) if p is not None]
    ep.update(
        {
            "outcome": outcome,
            "loss_at": loss_at,
            "loss_rise_at": loss_rise,
            "loss_exit_at": loss_exit,
            "returned_at": ret,
            "return_date_at": ret_date,
            "exit_at": exit_month,
            "first_event": min(events) if events else None,
            "balance_at_transfer": bal0,
            "followup_months": months_between(ep["start"], ep["end"]),
            "any_code_liquidation_exit": any_code_exit,
        }
    )
    return ep


def label_v1(ep):
    """v1's label, for the window-bug check: any realized loss > 0 in the window, etc."""
    rows, window = ep["rows"], ep["window"]
    if any((rows[p]["realized_loss"] or 0) > 0 for p in window):
        return "loss"
    if any(rows[p]["msr"] is not None and rows[p]["msr"] >= ep["transfer"] for p in window):
        return "returned"
    if window[-1] < ep["deal_last"]:
        return "left"
    return "open"


def outcome_report(episodes):
    with_c = [e for e in episodes if e["comments"]]

    def counts(es):
        return dict(Counter(e["outcome"] for e in es))

    return {
        "episodes": len(episodes),
        "episodes_with_comment": len(with_c),
        "outcomes_all": counts(episodes),
        "outcomes_with_comment": counts(with_c),
        "loss_by_route_with_comment": {
            "realized_loss_rise_only": sum(
                e["loss_rise_at"] is not None and e["loss_exit_at"] is None for e in with_c
            ),
            "liquidation_exit_only": sum(
                e["loss_exit_at"] is not None and e["loss_rise_at"] is None for e in with_c
            ),
            "both": sum(
                e["loss_exit_at"] is not None and e["loss_rise_at"] is not None for e in with_c
            ),
        },
        "loss_if_any_liquidation_code_before_exit_counted_with_comment": sum(
            e["outcome"] == "loss" or e["any_code_liquidation_exit"] is not None for e in with_c
        ),
        "cured_returned_by_code_8_or_9_without_return_date_with_comment": sum(
            e["outcome"] == "cured_returned" and e["return_date_at"] is None for e in with_c
        ),
        "open_with_comment_followup_ge_24": sum(
            e["outcome"] == "open" and e["followup_months"] >= 24 for e in with_c
        ),
        "open_with_comment_followup_lt_24": sum(
            e["outcome"] == "open" and e["followup_months"] < 24 for e in with_c
        ),
        "open_with_comment_cut_by_next_transfer": sum(
            e["outcome"] == "open" and e["end"] < e["deal_last"] for e in with_c
        ),
        "balance_at_transfer_missing": sum(e["balance_at_transfer"] is None for e in episodes),
    }


# ---------------------------------------------------------------------------------------------
# Analysis A: lead-lag
# ---------------------------------------------------------------------------------------------


def text_has(family, text):
    if family == "liquidation":
        return bool(LIQUIDATION.search(text))
    if family == "return":
        return bool(RETURN.search(text))
    if family == "workout_decided":
        return any(
            WORKOUT_DECIDED_TERMS.search(s) and not REQUEST.search(s) for s in sentences(text)
        )
    if family == "borrower_request":
        return any(REQUEST.search(s) and WORKOUT_REQUEST_TERMS.search(s) for s in sentences(text))
    if family in OTHER_FAMILIES:
        return bool(OTHER_FAMILIES[family].search(text))
    raise ValueError(family)


def jev_has(family, answers):
    ws = answers.get("workout_strategy", set())
    if family == "liquidation":
        return bool(ws & JEV_LIQUIDATION)
    if family == "return":
        return bool(ws & JEV_RETURN)
    if family == "workout_decided":
        return bool(ws & JEV_WORKOUT)
    if family == "borrower_request":
        return bool(answers.get("borrower_request"))
    raise ValueError(family)


def first_text(ep, family):
    return next((p for p in sorted(ep["comments"]) if text_has(family, ep["comments"][p])), None)


def first_jev(ep, family):
    return next((p for p in sorted(ep["jev"]) if jev_has(family, ep["jev"][p])), None)


def last_mod_before(ep):
    prev = None
    for p in ep["periods"]:
        if p >= ep["start"]:
            break
        c = code(ep["rows"][p]["mod_code"])
        if c and c != "0":
            prev = c
    return prev


def first_tape(ep, family):
    rows = ep["rows"]
    prev_mod = last_mod_before(ep) if family == "workout_decided" else None
    for p in ep["window"]:
        r = rows[p]
        ws = code(r["ws"])
        if family == "liquidation" and ws in TAPE_LIQUIDATION:
            return p
        if family == "return" and (
            ws in TAPE_RETURN or (r["msr"] is not None and r["msr"] >= ep["transfer"])
        ):
            return p
        if family == "borrower_request" and ws in TAPE_WORKOUT:
            return p
        if family == "workout_decided":
            mc = code(r["mod_code"])
            if ws in TAPE_WORKOUT or (mc and mc != "0" and mc != prev_mod):
                return p
    return None


def share_ci_by_deal(pairs, rng):
    """pairs: list of (deal, lead). CI on the share of text-first pairs, deal-cluster bootstrap."""
    by = defaultdict(list)
    for d, lead in pairs:
        by[d].append(lead)
    deals = sorted(by)
    pos = {d: sum(x > 0 for x in by[d]) for d in deals}
    n = {d: len(by[d]) for d in deals}
    shares = []
    for _ in range(N_BOOT):
        pick = rng.choice(len(deals), size=len(deals), replace=True)
        tot = sum(n[deals[i]] for i in pick)
        shares.append(sum(pos[deals[i]] for i in pick) / tot)
    return [float(np.percentile(shares, 2.5)), float(np.percentile(shares, 97.5))]


def lead_lag(episodes, measure, rng):
    text_first_fn = first_text if measure == "keywords" else first_jev
    out = {}
    eligible = [e for e in episodes if e["comments"]]
    for family in FAMILIES:
        pairs, text_only, tape_only, neither = [], 0, 0, 0
        for ep in eligible:
            a, b = text_first_fn(ep, family), first_tape(ep, family)
            if a and b:
                pairs.append((ep["deal"], months_between(a, b)))
            elif a:
                text_only += 1
            elif b:
                tape_only += 1
            else:
                neither += 1
        leads = np.array([x for _, x in pairs])
        k = int((leads > 0).sum()) if len(leads) else 0
        out[family] = {
            "episodes": len(eligible),
            "both": len(pairs),
            "text_first": k,
            "same_month": int((leads == 0).sum()) if len(leads) else 0,
            "tape_first": int((leads < 0).sum()) if len(leads) else 0,
            "median_lead_months": float(np.median(leads)) if len(leads) else None,
            "lead_iqr_months": [float(np.percentile(leads, 25)), float(np.percentile(leads, 75))]
            if len(leads)
            else None,
            "share_text_first": k / len(pairs) if pairs else None,
            "share_text_first_ci95_deal_bootstrap": share_ci_by_deal(pairs, rng) if pairs else None,
            "deals_with_pairs": len({d for d, _ in pairs}),
            "text_only": text_only,
            "tape_only": tape_only,
            "neither": neither,
        }
    return out


def keyword_precision(episodes):
    n, flagged = 0, 0
    per = Counter()
    for ep in episodes:
        p = first_text(ep, "liquidation")
        if p is None:
            continue
        text = ep["comments"][p]
        sent = next(s for s in sentences(text) if LIQUIDATION.search(s))
        n += 1
        hits = [name for name, rx in HEDGES.items() if rx.search(sent)]
        for h in hits:
            per[h] += 1
        flagged += bool(hits)
    return {
        "first_liquidation_hits": n,
        "negated_or_contingent": flagged,
        "share_negated_or_contingent": flagged / n if n else None,
        "share_ci95_wilson": wilson(flagged, n),
        "by_pattern": {k: per[k] for k in HEDGES},
        "unit": "the sentence holding the first liquidation keyword in the episode's first "
        "liquidation-keyword month",
    }


# ---------------------------------------------------------------------------------------------
# Analysis B: prediction at landmarks
# ---------------------------------------------------------------------------------------------


def tape_features(ep, t):
    rows = ep["rows"]
    p = max(q for q in ep["window"] if q <= t)
    r = rows[p]
    dscr = float(r["dscr"]) if r["dscr"] is not None else None
    occ = float(r["occupancy"]) if r["occupancy"] is not None else None
    fin = months_between(r["financials_end"], t) if r["financials_end"] is not None else None
    bal = float(r["ending_balance"]) if r["ending_balance"] is not None else None
    ws = code(r["ws"])
    mc = code(r["mod_code"])
    f = {
        "dscr": dscr if dscr is not None else 0.0,
        "dscr_missing": float(dscr is None),
        "occupancy": occ if occ is not None else 0.0,
        "occupancy_missing": float(occ is None),
        "months_since_financials_end": float(fin) if fin is not None else 0.0,
        "financials_end_missing": float(fin is None),
        "pay_rank": float(PAY_RANK.get(code(r["ps"]) or "0", 0)),
        "log_balance": math.log(bal) if bal and bal > 0 else 0.0,
        "months_since_transfer": float(months_between(ep["transfer"], t)),
        "ws_liquidation": float(ws in TAPE_LIQUIDATION),
        "ws_workout": float(ws in TAPE_WORKOUT),
        "modified": float(bool(mc) and mc != "0"),
    }
    pt = (r["property_type"] or "").strip().upper()
    for x in PROPERTY_TYPES:
        f[f"type_{x}"] = float(pt == x)
    return f


def keyword_features(ep, t):
    texts = [ep["comments"][p] for p in sorted(ep["comments"]) if p <= t]
    allt = " ".join(texts)
    f = {
        "kw_liquidation": float(text_has("liquidation", allt)),
        "kw_workout_request": float(any(text_has("borrower_request", s) for s in texts)),
    }
    for fam in OTHER_FAMILIES:
        f[f"kw_{fam}"] = float(text_has(fam, allt))
    f["kw_log_length"] = math.log(1 + len(allt))
    return f


def jev_features(ep, t):
    def latest(field):
        for p in sorted((q for q in ep["jev"] if q <= t), reverse=True):
            v = ep["jev"][p].get(field)
            if v:
                return v
        return set()

    ws, br, tr = latest("workout_strategy"), latest("borrower_request"), latest("transfer_reason")
    f = {
        "jev_ws_liquidation": float(bool(ws & JEV_LIQUIDATION)),
        "jev_ws_workout": float(bool(ws & JEV_WORKOUT)),
        "jev_ws_return": float(bool(ws & JEV_RETURN)),
        "jev_ws_other": float(bool(ws - JEV_LIQUIDATION - JEV_WORKOUT - JEV_RETURN)),
        "jev_borrower_request": float(bool(br)),
    }
    for reason in JEV_TRANSFER_REASONS:
        f[f"jev_transfer_{reason.replace(' ', '_').replace('-', '_')}"] = float(reason in tr)
    return f


def landmark_sample(episodes, L, open_mode):
    rows, why = [], Counter()
    for ep in episodes:
        t = add_months(ep["start"], L)
        if ep["first_event"] is not None and ep["first_event"] <= t:
            why["resolved_by_landmark"] += 1
            continue
        if t > ep["end"]:
            why["window_ended_before_landmark"] += 1
            continue
        if not any(p <= t for p in ep["comments"]):
            why["no_comment_by_landmark"] += 1
            continue
        if ep["outcome"] == "open":
            long = ep["followup_months"] >= OPEN_FOLLOWUP_MONTHS
            if open_mode == "sensitivity" and long:
                y = 0
                why["open_ge_24_as_not_loss"] += 1
            else:
                why["open_dropped_ge_24" if long else "open_dropped_lt_24"] += 1
                continue
        else:
            y = int(ep["outcome"] == "loss")
        rows.append(
            {
                "ep": ep,
                "t": t,
                "y": y,
                "deal": ep["deal"],
                "tape": tape_features(ep, t),
                "kw": keyword_features(ep, t),
                "jev": jev_features(ep, t),
            }
        )
    return rows, dict(why)


def oof_scores(X, y, groups):
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import GridSearchCV, GroupKFold
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    scores, chosen = np.zeros(len(y)), []
    for tr, te in GroupKFold(n_splits=5).split(X, y, groups):
        pipe = make_pipeline(StandardScaler(), LogisticRegression(max_iter=10000))
        search = GridSearchCV(
            pipe,
            {"logisticregression__C": list(C_GRID)},
            cv=GroupKFold(n_splits=5),
            scoring="roc_auc",
        )
        search.fit(X[tr], y[tr], groups=groups[tr])
        scores[te] = search.predict_proba(X[te])[:, 1]
        chosen.append(search.best_params_["logisticregression__C"])
    return scores, chosen


def predict_at(rows, rng):
    y = np.array([r["y"] for r in rows])
    g = np.array([r["deal"] for r in rows])
    tk, kk, jk = sorted(rows[0]["tape"]), sorted(rows[0]["kw"]), sorted(rows[0]["jev"])
    xt = np.array([[r["tape"][k] for k in tk] for r in rows])
    xk = np.array([[r["kw"][k] for k in kk] for r in rows])
    xj = np.array([[r["jev"][k] for k in jk] for r in rows])
    models = {"tape": xt, "tape_keywords": np.hstack([xt, xk]), "tape_jev": np.hstack([xt, xj])}
    scores, chosen = {}, {}
    for name, X in models.items():
        scores[name], chosen[name] = oof_scores(X, y, g)
    deals = sorted(set(g))
    idx = {d: np.where(g == d)[0] for d in deals}
    boot = defaultdict(list)
    skipped = 0
    for _ in range(N_BOOT):
        pick = np.concatenate(
            [idx[deals[i]] for i in rng.choice(len(deals), len(deals), replace=True)]
        )
        yb = y[pick]
        if yb.min() == yb.max():
            skipped += 1
            continue
        a = {m: auc(yb, scores[m][pick]) for m in models}
        for m in models:
            boot[f"auc_{m}"].append(a[m])
        boot["delta_keywords"].append(a["tape_keywords"] - a["tape"])
        boot["delta_jev"].append(a["tape_jev"] - a["tape"])
    ci = {k: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] for k, v in boot.items()}
    out = {
        "n": len(y),
        "losses": int(y.sum()),
        "deals": len(deals),
        "bootstrap_resamples_used": N_BOOT - skipped,
        "models": {},
    }
    for m in models:
        out["models"][m] = {
            "auc": auc(y, scores[m]),
            "auc_ci95": ci[f"auc_{m}"],
            "brier": float(np.mean((scores[m] - y) ** 2)),
            "C_by_outer_fold": chosen[m],
        }
    for m, key in (("tape_keywords", "delta_keywords"), ("tape_jev", "delta_jev")):
        out["models"][m]["delta_auc_vs_tape"] = (
            out["models"][m]["auc"] - out["models"]["tape"]["auc"]
        )
        out["models"][m]["delta_auc_ci95"] = ci[key]
        out["models"][m]["delta_auc_share_of_resamples_above_0"] = float(
            np.mean(np.array(boot[key]) > 0)
        )
    out["base_rate_brier"] = float(np.mean((y.mean() - y) ** 2))
    return out


def analysis_b(episodes, rng):
    eligible = [e for e in episodes if e["comments"]]
    res = {"primary": {}, "sensitivity_open_ge_24_as_not_loss": {}}
    samples = {}
    for mode, key in (("drop", "primary"), ("sensitivity", "sensitivity_open_ge_24_as_not_loss")):
        for L in LANDMARKS:
            rows, why = landmark_sample(eligible, L, mode)
            r = predict_at(rows, rng)
            r["sample_exclusions"] = why
            res[key][f"landmark_{L}"] = r
            samples[(mode, L)] = rows
    return res, samples


# ---------------------------------------------------------------------------------------------
# Analysis C: descriptive
# ---------------------------------------------------------------------------------------------


def analysis_c(rows):
    def table(split):
        cells = {}
        for label, on in (("text_names_liquidation", True), ("text_does_not", False)):
            sub = [r for r in rows if split(r) == on]
            k = sum(r["y"] for r in sub)
            cells[label] = {
                "n": len(sub),
                "losses": k,
                "loss_rate": k / len(sub) if sub else None,
                "wilson_ci95": wilson(k, len(sub)),
            }
        return cells

    no_tape_liq = [
        r
        for r in rows
        if not any(
            code(r["ep"]["rows"][p]["ws"]) in TAPE_LIQUIDATION
            for p in r["ep"]["window"]
            if p <= r["t"]
        )
    ]
    tape_liq = len(rows) - len(no_tape_liq)
    rows = no_tape_liq
    return {
        "landmark": 3,
        "sample": "Analysis B primary landmark-3 set, loans whose tape coded no liquidation by the landmark",
        "n": len(rows),
        "excluded_tape_already_coded_liquidation": tape_liq,
        "keywords": table(lambda r: r["kw"]["kw_liquidation"] == 1.0),
        "jev_added_not_in_plan": table(lambda r: r["jev"]["jev_ws_liquidation"] == 1.0),
    }


# ---------------------------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------------------------


def hand_check(episodes, path):
    rnd = random.Random(SEED)
    pick = []
    for outcome, k in (("loss", 3), ("cured_returned", 3), ("other_exit", 2), ("open", 2)):
        pool = [e for e in episodes if e["comments"] and e["outcome"] == outcome]
        pick += rnd.sample(pool, min(k, len(pool)))
    lines = []
    for e in pick:
        lines.append(
            f"===== {e['deal']} loan {e['loan']} transfer {e['transfer']} window {e['start']}..{e['end']} "
            f"deal_last {e['deal_last']} bal_at_transfer {e['balance_at_transfer']}"
        )
        lines.append(
            f"  LABEL {e['outcome']}: loss_rise {e['loss_rise_at']} loss_exit {e['loss_exit_at']} "
            f"returned {e['returned_at']} exit {e['exit_at']} first_event {e['first_event']}"
        )
        lo, hi = add_months(e["start"], -2), add_months(e["end"], 1)
        for p in e["periods"]:
            if lo <= p <= hi:
                r = e["rows"][p]
                mark = "*" if p in set(e["window"]) else " "
                lines.append(
                    f"  {mark}{p} ws={r['ws']} ps={r['ps']} tr={r['transfer']} msr={r['msr']} "
                    f"loss={r['realized_loss']} bal={r['ending_balance']}"
                )
    path.write_text("\n".join(lines) + "\n")
    return [
        {
            "deal": e["deal"],
            "loan": e["loan"],
            "transfer": str(e["transfer"]),
            "label": e["outcome"],
        }
        for e in pick
    ]


def window_bug_check(by_loan, deal_last, comment_text, jev_by):
    """Loss counts among episodes with a comment, v1 window vs fixed window, both labels."""
    out = {}
    for mode in ("v1bug", "fixed"):
        eps = build_episodes(by_loan, deal_last, comment_text, jev_by, window_mode=mode)
        wc = [e for e in eps if e["comments"]]
        out[f"v2_cleaning_{mode}_window_v1_label_loss"] = sum(label_v1(e) == "loss" for e in wc)
        out[f"v2_cleaning_{mode}_window_v2_label_loss"] = sum(
            label_v2(e)["outcome"] == "loss" for e in wc
        )
        out[f"v2_cleaning_{mode}_window_episodes_with_comment"] = len(wc)
    # The exact v1 pipeline (v1 cleaning), with and without the window fix, v1 label.
    os.environ.setdefault("DATABASE_URL", DSN)
    sys.path.insert(0, str(HERE))
    import analyze as v1

    tape, comments, last, _ = v1.load()
    eps = v1.build(tape, comments, last)
    wc = [e for e in eps if e["comments"]]
    out["v1_pipeline_v1_window_v1_label_loss"] = sum(e["outcome"] == "loss" for e in wc)
    by = defaultdict(list)
    for e in eps:
        by[(e["deal"], e["loan"])].append(e)
    fixed_loss = fixed_loss_any_comment = n_fixed_wc = 0
    for es in by.values():
        es.sort(key=lambda e: e["start"])
        for i, e in enumerate(es):
            nstart = es[i + 1]["start"] if i + 1 < len(es) else None
            w = [p for p in sorted(e["rows"]) if p >= e["start"] and (nstart is None or p < nstart)]
            loss = any((e["rows"][p][7] or 0) > 0 for p in w)
            com = [p for p in e["comments"] if p in set(w)]
            if e["comments"]:
                fixed_loss += loss
            if com:
                n_fixed_wc += 1
                fixed_loss_any_comment += loss
    out["v1_pipeline_fixed_window_v1_label_loss_same_episodes"] = fixed_loss
    out["v1_pipeline_fixed_window_v1_label_loss_comment_in_fixed_window"] = fixed_loss_any_comment
    return out


# ---------------------------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------------------------


def fmt_ci(ci, pct=False, signed=False):
    if ci is None or ci[0] is None:
        return "n/a"
    f = (
        (lambda x: f"{100 * x:.0f}%")
        if pct
        else ((lambda x: f"{x:+.3f}") if signed else (lambda x: f"{x:.3f}"))
    )
    return f"[{f(ci[0])}, {f(ci[1])}]"


def write_markdown(res, path):
    L = []
    d, c, o = res["data"], res["cleaning"], res["outcomes"]
    L += [
        "# Lead-lag study v2: results",
        "",
        f"Run once, as fixed in PLAN-v2.md. Seed {SEED}; {N_BOOT} deal-cluster bootstrap resamples.",
        "",
        "## Data",
        "",
        f"- Deals loaded: {d['deals_loaded']} (vintages {d['vintages']}); tape {d['tape_periods'][0]} to "
        f"{d['tape_periods'][1]}, comments {d['comment_periods'][0]} to {d['comment_periods'][1]}.",
        f"- Loan-months {d['loan_months']:,}; comments {d['comments']:,}; Jev extractions {d['jev_extractions']:,}.",
        f"- Deals that failed to load: {d['deals_failed']} ({', '.join(f'{v} {k}' for k, v in d['deals_failed_by_reason'].items())}).",
        "",
        "## Cleaning",
        "",
        f"- Wrong-deal tapes (modal-name rule): {c['wrong_deal']['deal_months_dropped']} deal-months dropped "
        f"({c['wrong_deal']['loan_months_dropped']:,} loan-months, {c['wrong_deal']['deals_affected']} deals); "
        f"{c['wrong_deal']['deal_months_dropped_that_are_the_deals_first_month']} are a deal's first month. "
        f"Versus v1: {len(c['wrong_deal']['new_vs_v1'])} newly dropped, {len(c['wrong_deal']['v1_only'])} dropped by v1 only.",
        f"- Placeholder comments dropped: {c['comments']['placeholder_comments_dropped']} of {c['comments']['comments_total']:,}.",
        f"- Comment loan IDs: {c['comments']['joined_directly']:,} joined directly, "
        f"{c['comments']['joined_by_stripping_note_letter']} joined by stripping a note letter, "
        f"{c['comments']['still_unjoined']} still unjoined (excluded).",
        "",
        "## Outcomes (episodes with at least one comment in the window)",
        "",
        f"- Episodes: {o['episodes']:,}; with a comment: {o['episodes_with_comment']:,}.",
        "- " + ", ".join(f"{k} {v}" for k, v in sorted(o["outcomes_with_comment"].items())) + ".",
        f"- Loss route: realized-loss rise only {o['loss_by_route_with_comment']['realized_loss_rise_only']}, "
        f"liquidation exit only {o['loss_by_route_with_comment']['liquidation_exit_only']}, both {o['loss_by_route_with_comment']['both']}.",
        f"- Cured/returned on code 8 or 9 with no return date: {o['cured_returned_by_code_8_or_9_without_return_date_with_comment']}. "
        f"Open: {o['open_with_comment_followup_ge_24']} with >= 24 months follow-up, {o['open_with_comment_followup_lt_24']} with < 24; "
        f"{o['open_with_comment_cut_by_next_transfer']} of the open were cut by a later transfer.",
        "",
        "## Analysis A: lead-lag (months of publication; lead = tape month minus text month)",
        "",
    ]
    for measure in ("keywords", "jev"):
        L += [
            f"### Text by {'keywords' if measure == 'keywords' else 'Jev auto-accepted workout strategy / borrower request'}",
            "",
            "| Family | Both | Text first | Same | Tape first | Median lead (IQR) | Text-first share [95% CI] | Text only | Tape only |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for fam, r in res["analysis_a"][measure].items():
            iqr = r["lead_iqr_months"]
            L.append(
                f"| {fam} | {r['both']} | {r['text_first']} | {r['same_month']} | {r['tape_first']} | "
                f"{r['median_lead_months']:+.0f} ({iqr[0]:+.0f}, {iqr[1]:+.0f}) | "
                f"{100 * r['share_text_first']:.0f}% {fmt_ci(r['share_text_first_ci95_deal_bootstrap'], pct=True)} | "
                f"{r['text_only']} | {r['tape_only']} |"
                if r["both"]
                else f"| {fam} | 0 | | | | | | {r['text_only']} | {r['tape_only']} |"
            )
        L.append("")
    kp = res["keyword_precision"]
    L += [
        "### Keyword precision (liquidation)",
        "",
        f"{kp['negated_or_contingent']} of {kp['first_liquidation_hits']} first liquidation hits "
        f"({100 * kp['share_negated_or_contingent']:.0f}%, Wilson {fmt_ci(kp['share_ci95_wilson'], pct=True)}) sit in a sentence "
        f"with a hedge: " + ", ".join(f"'{k}' {v}" for k, v in kp["by_pattern"].items()) + ".",
        "",
        "## Analysis B: prediction of loss at landmarks",
        "",
        "Six primary comparisons: 3 landmarks x (tape + keywords, tape + Jev) against tape.",
        "",
    ]
    for key, title in (
        ("primary", "Primary (open episodes dropped)"),
        ("sensitivity_open_ge_24_as_not_loss", "Sensitivity (open >= 24 months = not loss)"),
    ):
        L += [
            f"### {title}",
            "",
            "| Landmark | n (losses, deals) | Model | AUC [95% CI] | dAUC vs tape [95% CI] | Brier |",
            "|---|---|---|---|---|---|",
        ]
        for lk, r in res["analysis_b"][key].items():
            for m, mr in r["models"].items():
                delta = (
                    f"{mr['delta_auc_vs_tape']:+.3f} {fmt_ci(mr['delta_auc_ci95'], signed=True)}"
                    if "delta_auc_vs_tape" in mr
                    else ""
                )
                L.append(
                    f"| {lk.split('_')[1]} | {r['n']} ({r['losses']}, {r['deals']}) | {m} | "
                    f"{mr['auc']:.3f} {fmt_ci(mr['auc_ci95'])} | {delta} | {mr['brier']:.3f} |"
                )
        L.append("")
        L.append(
            "Sample exclusions: "
            + "; ".join(
                f"landmark {lk.split('_')[1]}: "
                + ", ".join(f"{k} {v}" for k, v in sorted(r["sample_exclusions"].items()))
                for lk, r in res["analysis_b"][key].items()
            )
            + "."
        )
        L.append("")
    ac = res["analysis_c"]
    L += [
        "## Analysis C: landmark 3, tape not yet coded liquidation",
        "",
        f"n = {ac['n']} (excluded {ac['excluded_tape_already_coded_liquidation']} whose tape already coded liquidation).",
        "",
        "| Split | Text names liquidation: loss / n (rate, Wilson 95%) | Text does not |",
        "|---|---|---|",
    ]
    for k, title in (
        ("keywords", "Keywords (plan)"),
        ("jev_added_not_in_plan", "Jev (added, not in plan)"),
    ):
        a, b = ac[k]["text_names_liquidation"], ac[k]["text_does_not"]
        cell = lambda x: (
            f"{x['losses']} / {x['n']} ({100 * x['loss_rate']:.0f}%, {fmt_ci(x['wilson_ci95'], pct=True)})"
            if x["n"]
            else "0 / 0"
        )
        L.append(f"| {title} | {cell(a)} | {cell(b)} |")
    wb = res["sanity"]["window_bug"]
    L += [
        "",
        "## Sanity checks",
        "",
        f"- Window bug, exact v1 pipeline and v1 label: {wb['v1_pipeline_v1_window_v1_label_loss']} losses with the bug; "
        f"{wb['v1_pipeline_fixed_window_v1_label_loss_same_episodes']} with contiguous windows (same episodes); "
        f"{wb['v1_pipeline_fixed_window_v1_label_loss_comment_in_fixed_window']} counting episodes with a comment in the fixed window.",
        f"- Window bug, v2 cleaning: v1 label {wb['v2_cleaning_v1bug_window_v1_label_loss']} (bug) -> "
        f"{wb['v2_cleaning_fixed_window_v1_label_loss']} (fixed); v2 label {wb['v2_cleaning_v1bug_window_v2_label_loss']} (bug) -> "
        f"{wb['v2_cleaning_fixed_window_v2_label_loss']} (fixed).",
        "- Hand check of 10 episodes: traces in handcheck_v2.txt; verdicts below.",
        "",
        "## Deviations and readings of the plan",
        "",
    ]
    L += [f"{i}. {x}" for i, x in enumerate(DEVIATIONS, 1)]
    path.write_text("\n".join(L) + "\n")


# ---------------------------------------------------------------------------------------------


def main():
    rng = np.random.default_rng(SEED)
    tape, comments, jev, jev_extractions, run_months = load()
    data = describe_data(tape, comments, jev_extractions, run_months)
    tape, wrong_deal = clean_wrong_deal_tapes(tape)
    comment_text, jev_by, comment_report = clean_comments(comments, jev, tape)
    by_loan, deal_last = index_tape(tape)
    episodes = [label_v2(e) for e in build_episodes(by_loan, deal_last, comment_text, jev_by)]
    with_c = [e for e in episodes if e["comments"]]
    res = {
        "data": data,
        "cleaning": {"wrong_deal": wrong_deal, "comments": comment_report},
        "outcomes": outcome_report(episodes),
        "analysis_a": {
            "keywords": lead_lag(with_c, "keywords", rng),
            "jev": lead_lag(with_c, "jev", rng),
        },
        "keyword_precision": keyword_precision(with_c),
    }
    res["analysis_b"], samples = analysis_b(episodes, rng)
    res["analysis_c"] = analysis_c(samples[("drop", 3)])
    res["sanity"] = {
        "hand_check_episodes": hand_check(episodes, HERE / "handcheck_v2.txt"),
        "window_bug": window_bug_check(by_loan, deal_last, comment_text, jev_by),
    }
    res["deviations"] = DEVIATIONS
    (HERE / "results_v2.json").write_text(json.dumps(res, indent=1, default=str))
    write_markdown(res, HERE / "results_v2.md")
    print((HERE / "results_v2.md").read_text())


if __name__ == "__main__":
    main()
