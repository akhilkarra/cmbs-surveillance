"""The monthly 10-D distribution report (EX-99.1): special-servicing comments, appraisal
reductions and delinquency detail per loan, which the ABS-EE loan tape does not carry.

`grid` flattens the HTML into section titles and table rows, `layouts` says which tables each
certificate administrator's format has and how their columns are named, `parse` turns a report
into typed records without touching the database, and `service` stores them.
"""
