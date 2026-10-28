# Distribution report fixtures

## Real reports, redacted

The two files under accession folders are real 10-D EX-99.1 distribution reports downloaded
from SEC EDGAR. **The committed copies are redacted, not byte-exact.** Personal contact details
on each report's first page were replaced:

| Replaced | Placeholder |
|---|---|
| An individual's name | `[name redacted]` |
| A personal email address | `redacted@example.invalid` |
| A personal phone number | `[phone redacted]` |

Everything else is unchanged byte for byte:

- company names, group mailboxes and desk lines;
- deal names and CUSIPs;
- every table, value and comment.

Parsing a redacted copy gives exactly the same result as parsing the original.

### `0001888524-26-017704/wcm18b13_ex991-202609.htm`

BANK 2018-BNK13, September 2026, Computershare format.

| | |
|---|---|
| SEC URL | https://www.sec.gov/Archives/edgar/data/1744707/000188852426017704/wcm18b13_ex991-202609.htm |
| As downloaded | 1,572,179 bytes, SHA-256 `021cedaec51c2b009431bd10f9eb73552bfd391a4203b83df12ea4556e621036` |
| Committed copy | 1,572,183 bytes, SHA-256 `dc4ab76aa12e397f142aa3e010d439b20c281a9bb97f13164f2638b5a474a61f` |
| Redacted | 7 replacements in the "Contacts" table on page 1: 3 names, 2 phone numbers, 2 email addresses |

The redacted contacts are the depositor's, the master and special servicer's, and the special servicer's named individuals. Group mailboxes, the trustee's "CMBS Trustee" desk line and the administrator's service number are kept.

### `0001628297-26-000742/ex99_1.htm`

CGCMT 2016-C1, September 2026, Citi format.

| | |
|---|---|
| SEC URL | https://www.sec.gov/Archives/edgar/data/1673255/000162829726000742/ex99_1.htm |
| As downloaded | 777,983 bytes, SHA-256 `1c07a05fd972b4084fa325f1915ca2a882ac002fd4b6a0bfcafec0a4021ffb18` |
| Committed copy | 777,996 bytes, SHA-256 `16c29d8ad32ac1e6464244dd3d0e820505dd331a5013a8923692034695fd2caa` |
| Redacted | 3 replacements in the certificate administrator contact block on page 1: 1 name, 1 phone number, 1 email address |

## Synthetic report

`fixture-trust-2026-f1_10d_2026-09.htm` is written for the tests, for the fixture deal "Fixture Trust 2026-F1". It follows the Computershare layout. It is not a filed document.
