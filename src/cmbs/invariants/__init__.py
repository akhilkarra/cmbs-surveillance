"""Pool identities that hold for every deal and period, each proved in Lean for every pool.

- `partition`: delinquency buckets cover every loan exactly once and add up to the pool;
- `wac`: the weighted-average coupon lies between the lowest and highest loan rates;
- the balance roll-forward lives with the other checks, in `cmbs.crosscheck.rollforward`.

The proofs and the oracle the differential tests compare against are in `lean/` (grill log Q4).
"""
