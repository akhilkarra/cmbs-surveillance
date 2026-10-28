"""The base of every domain refusal.

A refusal is the domain declining to do something, with a message written for the user:
"pm may not decide review items", "run 42 already finished as 'succeeded'". Scenario steps
capture refusals (`cmbs.testing.bdd.attempt`) and the API returns them as readable responses.
Programming errors (a bad argument, a broken invariant) stay ordinary exceptions.
"""


class Refused(Exception):
    """An action the domain will not perform. The message is shown to the user as written."""
