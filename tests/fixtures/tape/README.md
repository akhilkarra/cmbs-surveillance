# Loan tape fixtures

Real SEC files used by the loan tape, point-in-time and staleness tests (grill log Q69).

## EX-102 asset files

Downloaded from www.sec.gov/Archives on 2026-10-28 with the project's contact user agent. Each
is stored gzip-compressed (`gzip -n -9`) so pre-commit's text fixers cannot change a byte;
`tapes.py` checks the decompressed bytes against the original SHA-256 every time a test reads
one. Nothing is redacted: the files name companies (originators, servicers, borrowers'
properties) but no person, email address or phone number.

| Committed file | Filing | Original | Original SHA-256 | Size (bytes) | Committed SHA-256 |
|---|---|---|---|---|---|
| `bank-2018-bnk13_2026-08_ex102.xml.gz` | BANK 2018-BNK13, ABS-EE, August 2026, filed 2026-08-27 | https://www.sec.gov/Archives/edgar/data/1744707/000188852426015516/exh_102.xml | `1c261ffca8fddbe26c87c4aff80f22c2b8fafc84f7b50513612b916ed4791707` | 474,650 | `7ccb43a299d3ad45bc231124a4e4bc37bd89c02dd52a9506b0d76b3ea24034ec` |
| `bank-2018-bnk13_2026-09_ex102.xml.gz` | BANK 2018-BNK13, ABS-EE, September 2026, filed 2026-09-29 | https://www.sec.gov/Archives/edgar/data/1744707/000188852426017705/exh_102.xml | `18694fdec15d13583b9a6c933a6d2b1be107a05e05dbb4ed74620def41fb15ea` | 334,517 | `fc618bb3f00453e408e604b4822b3fb0f1947dd720943f0022ba5ef3ea03fe49` |
| `bank5-2026-5yr23_2026-09_ex102.xml.gz` | BANK5 2026-5YR23, ABS-EE, September 2026, filed 2026-10-01 | https://www.sec.gov/Archives/edgar/data/2140978/000188852426018152/exh_102.xml | `dc5a19686305cde375f68e76d7838f059abfa8c8e3c73cfe2988529b3c679687` | 483,466 | `1fa0288c88150fc6f82c6355f696f62d08360e06cded321d4a9033e65262fe16` |

The September BNK13 file is byte for byte the copy the 10-D (0001888524-26-017704) attaches,
and the file the extracts in `data/samples/bank-2018-bnk13/` came from.

## Submissions JSON

`submissions_CIK0001744707.json` and `submissions_CIK0002140978.json` come from
`https://data.sec.gov/submissions/CIK<cik>.json`, fetched 2026-10-28. Only the filer identity
and five `filings.recent` columns are kept (accession number, filing date, report date, form,
primary document); each file's `_provenance` key says so.
