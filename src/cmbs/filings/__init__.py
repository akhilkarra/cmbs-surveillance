"""The registry of SEC filings and the exact files downloaded from them.

Every value in the panel traces back to a filing (accession number, form, filing date) and a
stored file (name, SHA-256, bytes). The filing date is when a value became public, which is
what point-in-time queries key on.
"""
