"""Sample deal universe: display name mapped to an EDGAR full-text search query.

Quoted phrases keep efts from tokenizing tickers into unrelated matches.
Some SASB tickers only match on the series suffix (for example "2017-ASHF").
"""

SAMPLE_DEALS: dict[str, str] = {
    "BANK5 2026-5YR23": '"BANK5 2026-5YR23"',
    "WFRBS 2014-C25": '"WFRBS Commercial Mortgage Trust 2014-C25"',
    "WFRBS 2013-C15": '"WFRBS Commercial Mortgage Trust 2013-C15"',
    "COMM 2014-CR18": '"COMM 2014-CR18"',
    "CGCMT 2016-C1": '"Citigroup Commercial Mortgage Trust 2016-C1"',
    "HYT 2024-RGCY": '"HYT 2024-RGCY"',
    "KSL 2026-HT3": '"KSL 2026-HT3"',
    "BX 2024-VLT5": '"BX 2024-VLT5"',
    "MSC 2017-ASHF": '"2017-ASHF"',
    "SMR 2022-IND": '"SMR 2022-IND"',
    "ILPT 2025-LPF2": '"ILPT 2025-LPF2"',
    "GSMS 2020-DUNE": '"2020-DUNE"',
    "CSMC 2020-FACT": '"2020-FACT"',
    "JW 2024-BERY": '"2024-BERY"',
    "Benchmark 2026-B43": '"Benchmark 2026-B43"',
}
