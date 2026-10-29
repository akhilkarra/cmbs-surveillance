"""Special-servicing comments turned into typed fields, each backed by one quoted sentence.

`sentences` numbers a comment's sentences and checks evidence against them; `fields` defines
the typed fields and reads their values; `questions` holds every question asked of the model
(prompt version 3) and reads the answers into judgments; `decision` is the truth table that
sends each judgment to the panel or to review; `service` runs it all against the database.
"""
