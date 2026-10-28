"""Each certificate administrator's report format: how to recognise it, its section titles, and
the tables that carry comments, appraisal reductions and delinquency detail.

Computershare (formerly Wells Fargo CTS) prints a free-text "Special Servicing Comments" column
in "Specially Serviced Loan Detail - Part 2". Citi prints no comment text at all and points
readers to its portal (research/11-data-sources.md, section 2).

Column patterns are searched in header labels (see `grid.header_label`): lower case, footnote
markers removed, "P & I" written "p&i".
"""

import re
from enum import StrEnum

from cmbs.distribution.extract import LOAN, Column, TableSpec
from cmbs.distribution.grid import title_key


class Administrator(StrEnum):
    COMPUTERSHARE = "computershare"
    CITI = "citi"


# A link to the administrator's investor site is printed on its reports; the name alone can
# appear in another role (a trustee, a servicer), so it only decides when no link is found.
_LINKS = {Administrator.COMPUTERSHARE: "ctslink.com", Administrator.CITI: "citidirect.com"}
_NAMES = {Administrator.COMPUTERSHARE: "computershare", Administrator.CITI: "citibank"}


def _only(found: set[Administrator]) -> Administrator | None:
    return next(iter(found)) if len(found) == 1 else None


def detect(text: str) -> Administrator | None:
    """The administrator whose format this report text is in, or None if unclear."""
    lowered = text.lower()
    by_link = {admin for admin, link in _LINKS.items() if link in lowered}
    if by_link:
        return _only(by_link)
    return _only({admin for admin, name in _NAMES.items() if name in lowered})


def _pattern(text: str) -> re.Pattern[str]:
    return re.compile(text)


PROSPECTUS_ID = Column(
    LOAN,
    _pattern(
        r"\bpros(pectus)?\.?\s*(loan\s*)?(id\b|#|number\b|no\b)"
        r"|\bodcr\b|\bomcr\b|cross[- ]?reference"
    ),
    required=True,
)

COMMENT_COLUMN = Column("comment", _pattern(r"comment"), required=True, free_text=True)

APPRAISAL_COLUMNS = (
    PROSPECTUS_ID,
    Column(
        "amount",
        _pattern(r"appraisal reduction|\bara\b"),
        required=True,
        exclude=_pattern(r"\bdate\b|histor|\basers?\b"),
    ),
    Column("reduction_date", _pattern(r"(appraisal reduction|\bara\b).*\bdate\b")),
)

DELINQUENCY_COLUMNS = (
    PROSPECTUS_ID,
    Column("status", _pattern(r"\bstatus\b"), required=True, exclude=_pattern(r"resolution")),
    Column("paid_through", _pattern(r"paid (through|thru)|\bptd\b")),
    # "Current P&I Advances", or "P & I Advances" over "Current": the words in either order.
    Column("current_advances", _pattern(r"^(?=.*\bcurrent\b)(?=.*p&i adv)")),
    Column("outstanding_advances", _pattern(r"^(?=.*\boutstanding\b)(?=.*p&i adv)")),
    Column("actual_balance", _pattern(r"actual (principal )?bal")),
)

SPECIAL_SERVICING_COMMENTS = TableSpec(
    "Specially Serviced Loan Detail - Part 2", (PROSPECTUS_ID, COMMENT_COLUMN)
)
APPRAISAL_REDUCTION_DETAIL = TableSpec("Appraisal Reduction Detail", APPRAISAL_COLUMNS)
COMPUTERSHARE_APPRAISALS = (
    APPRAISAL_REDUCTION_DETAIL,
    TableSpec("Mortgage Loan Detail (Part 2)", APPRAISAL_COLUMNS),
)
DELINQUENCY = TableSpec("Delinquency Loan Detail", DELINQUENCY_COLUMNS)

COMMENT_TABLES: dict[Administrator, tuple[TableSpec, ...]] = {
    Administrator.COMPUTERSHARE: (SPECIAL_SERVICING_COMMENTS,),
    Administrator.CITI: (),
}
APPRAISAL_TABLES: dict[Administrator, tuple[TableSpec, ...]] = {
    Administrator.COMPUTERSHARE: COMPUTERSHARE_APPRAISALS,
    Administrator.CITI: (APPRAISAL_REDUCTION_DETAIL,),
}
DELINQUENCY_TABLES: dict[Administrator, tuple[TableSpec, ...]] = {
    Administrator.COMPUTERSHARE: (DELINQUENCY,),
    Administrator.CITI: (DELINQUENCY,),
}

# Every section title either format prints. Each one ends the section before it, so a table is
# never read under the wrong title; a title missing here would let one section run into the next.
SECTION_TITLES = (
    # Computershare, from the BANK 2018-BNK13 table of contents.
    "Certificate Distribution Detail",
    "Certificate Factor Detail",
    "Certificate Interest Reconciliation Detail",
    "Additional Information",
    "Bond/Collateral Reconciliation - Cash Flows",
    "Bond/Collateral Reconciliation - Balances",
    "Current Mortgage Loan and Property Stratification",
    "Mortgage Loan Detail (Part 1)",
    "Mortgage Loan Detail (Part 2)",
    "Principal Prepayment Detail",
    "Historical Detail",
    "Delinquency Loan Detail",
    "Collateral Stratification and Historical Detail",
    "Specially Serviced Loan Detail - Part 1",
    "Specially Serviced Loan Detail - Part 2",
    "Modified Loan Detail",
    "Historical Liquidated Loan Detail",
    "Historical Bond/Collateral Loss Reconciliation Detail",
    "Interest Shortfall Detail - Collateral Level",
    "Supplemental Notes",
    # Citi.
    "Distribution Summary",
    "Distribution Summary (Factors)",
    "Interest Distribution Detail",
    "Principal Distribution Detail",
    "Reconciliation Detail",
    "Stratification Detail",
    "Mortgage Loan Detail",
    "NOI Detail",
    "Appraisal Reduction Detail",
    "Historical Appraisal Reduction Detail",
    "Loan Modification Detail",
    "Historical Loan Modification Detail",
    "Specially Serviced Loan Detail",
    "Liquidated Loan Detail",
    "CREFC Investor Reporting Package Legends",
)
TITLE_KEYS = frozenset(title_key(title) for title in SECTION_TITLES)
