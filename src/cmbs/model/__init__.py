"""Asking a decision model typed questions about text, behind one provider-neutral interface.

`types` holds the questions and answers as they travel (TypeSafe's wire format, which other
providers adapt to); `policy` the pure rules (pinned versions, what may be sent, the recording
key); `providers` the live providers; `recordings` the record-and-replay store; `client` puts
them together. Every answer the panel uses is recorded, and tests only ever replay (grill log
Q7, Q16, Q42).
"""
