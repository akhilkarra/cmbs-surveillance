# Live Jev answers

Real answers from TypeSafe's Jev (`jev-1.14.0`), recorded on 2026-10-29 by sending each comment
once through `cmbs.model.providers.Jev`, with the questions `cmbs.extraction.questions.plan`
builds (prompt version 3). Each file holds the comment, the exact request sent and the response
received (billing usage removed).

| File | Comment |
|---|---|
| `fixture-loan-2.json` | The fixture deal's loan 2 comment (written for the tests) |
| `bnk13-loan-11.json` | BANK 2018-BNK13, September 2026 10-D, loan 11 (Ditson Building), public SEC filing |
| `bnk13-loan-57.json` | BANK 2018-BNK13, September 2026 10-D, loan 57, public SEC filing |
| `hard-no-reason-no-request.json` | Written to state no transfer reason, request or strategy |
| `hard-request-granted.json` | Written so the borrower's request is granted, not pending |

`tests/test_model_live.py` checks that today's questions are exactly the ones recorded (wording
drift fails the test) and that the decision table turns these answers into the outcomes an
analyst would choose. These are not synthetic: unlike `fixture-f1-loan-2-2026-09.json`, they can
be imported as recordings. The BNK13 comments keep the filer's own broken words ("Avenu e",
"t he"); see MESS-REGISTER M-025.
