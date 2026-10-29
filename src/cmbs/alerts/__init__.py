"""Monthly surveillance alerts and their shared lifecycle.

Each month a deal's loans, as published, are compared with the month before; every change worth
a PM's attention is stored once as an alert with a severity, the filing it came from and the
settings version that produced it. See features/surveillance/monthly_alerts.feature and
alert_lifecycle.feature (grill log Q25, Q44, Q46).
"""
