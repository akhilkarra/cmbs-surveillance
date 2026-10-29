# Alert fixtures

Synthetic files for the surveillance scenarios (`features/surveillance/monthly_alerts.feature`,
`alert_lifecycle.feature`, `book.feature`) and `tests/test_alert*.py`. None is a filed document.

| File | What it is |
|---|---|
| `fixture_10d_template.htm` | A one-month distribution report (EX-99.1) in the Computershare layout, holding only the "Specially Serviced Loan Detail - Part 2" comments table. `features/steps/alerts.py` fills in the deal, the distribution and determination dates, and one row per loan comment. |

The loan tapes for each month are made from `tests/fixtures/schedule_al_minimal.xml` (the
September 2026 tape of "Fixture Trust 2026-F1"): the reporting period end moves to the month's
11th and every paid-through date moves by the same number of months, so a month made this way
shows no change from the month before until a scenario changes it. Fixture deals other than
"Fixture Trust 2026-F1" reuse the same loans under their own synthetic CIK.
