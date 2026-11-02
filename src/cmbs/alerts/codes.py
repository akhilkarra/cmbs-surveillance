"""Schedule AL code lists, with the readable names alerts print. The one place codes are named.

Codes are the values the CMBS asset-data XML reports (`paymentStatusLoanCode`,
`workoutStrategyCode`), from the SEC's Schedule AL code lists for CMBS (Regulation AB II,
Item 1125); research/11-data-sources.md lists the values seen in real tapes.

Payment status is ordered by how far behind the loan is. The list itself puts the matured
balloons last, after "90+ days", but a matured balloon that is still paying is not worse than
a loan 90 days behind, so each code carries a rank:

    current < in grace period < late, under 30 days < 30 days < 60 days < 90+ days
                                                    < non-performing matured balloon

and a performing matured balloon ranks with "30 days": the loan has missed its maturity
payment but keeps paying, the first stage of a maturity default. Two different codes of equal
rank are neither better nor worse than each other, but the change is still reported.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Code:
    name: str
    rank: int = 0


PAYMENT_STATUS: dict[str, Code] = {
    "0": Code("current", 0),
    "A": Code("in grace period", 1),
    "B": Code("late, under 30 days", 2),
    "1": Code("30 days", 3),
    "2": Code("60 days", 4),
    "3": Code("90+ days", 5),
    "4": Code("performing matured balloon", 3),
    "5": Code("non-performing matured balloon", 6),
}

WORKOUT_STRATEGY: dict[str, Code] = {
    "1": Code("modification"),
    "2": Code("foreclosure"),
    "3": Code("bankruptcy"),
    "4": Code("extension"),
    "5": Code("note sale"),
    "6": Code("discounted payoff"),
    "7": Code("REO"),
    "8": Code("resolved"),
    "9": Code("pending return to master servicer"),
    "10": Code("deed in lieu of foreclosure"),
    "11": Code("full payoff"),
    "12": Code("reps and warranties"),
    "13": Code("to be determined"),
    "98": Code("other"),
}

# `propertyTypeCode`, as the certificate administrator's legend names them (Computershare's
# "Property Type Codes", BANK 2018-BNK13 10-D 0001888524-26-019174, page 15).
PROPERTY_TYPE: dict[str, Code] = {
    "HC": Code("health care"),
    "MU": Code("mixed use"),
    "WH": Code("warehouse"),
    "MF": Code("multi-family"),
    "SS": Code("self storage"),
    "LO": Code("lodging"),
    "RT": Code("retail"),
    "SF": Code("single family rental"),
    "98": Code("other"),
    "IN": Code("industrial"),
    "OF": Code("office"),
    "MH": Code("mobile home park"),
    "SE": Code("securities"),
    "CH": Code("cooperative housing"),
    "ZZ": Code("missing information/undefined"),
}

NOT_REPORTED = "not reported"


def _key(code: str) -> str:
    """Codes as reported may carry spaces, a lower-case letter or a leading zero ("01")."""
    text = code.strip().upper()
    return (text.lstrip("0") or "0") if text.isdigit() else text


def lookup(codes: dict[str, Code], code: str | None) -> Code | None:
    """The code's entry, or None when the code is not reported or not on the list."""
    if code is None:
        return None
    return codes.get(_key(code))


def name_of(codes: dict[str, Code], code: str | None) -> str:
    """'11' -> 'full payoff'; None -> 'not reported'; a code not on the list -> 'code 42'."""
    if code is None:
        return NOT_REPORTED
    found = lookup(codes, code)
    return found.name if found is not None else f"code {code.strip()}"


def same_code(first: str | None, second: str | None) -> bool:
    """Whether two reported codes mean the same thing: '01' is '1', 'b' is 'B'."""
    if first is None or second is None:
        return first is second
    return _key(first) == _key(second)
