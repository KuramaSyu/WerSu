"""Composite repo contracts (facades).

Facades compose one or more storage repos behind a single interface so
the service layer doesn't need to inject every repo individually.
Concrete implementations live under :mod:`src.db.repos`.
"""