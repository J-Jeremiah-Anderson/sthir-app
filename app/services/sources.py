"""Income-source intelligence: reliability scoring, shared reputation, and
fraud-ring detection.

For invoice-type sources this still carries the statutory-leverage tools
(MSMED s.16 interest, s.43B(h) tax exposure) because those genuinely apply and
give a worker real leverage against a client who pays late. For gig platforms
it is a pure reliability score. Ring detection - money cycling through a set of
parties - carries over unchanged as fraud defence.
"""
from __future__ import annotations

import datetime as dt
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import IncomeEvent, IncomeSource, Worker

RBI_BANK_RATE = float(os.getenv("FF_RBI_BANK_RATE", "0.0600"))
CORPORATE_TAX_RATE = float(os.getenv("FF_CORPORATE_TAX_RATE", "0.25"))

GRADES = [(0.95, "A"), (0.85, "B"), (0.70, "C"), (0.50, "D")]


def scorecard(db: Session, source: IncomeSource) -> dict:
    r = source.reliability
    grade = "-" if source.payments_observed == 0 else \
        next((g for t, g in GRADES if r >= t), "E")
    workers = db.execute(select(IncomeEvent.worker_id).where(
        IncomeEvent.source_id == source.id).distinct()).scalars().all()
    return {
        "id": source.id, "name": source.name, "kind": source.kind,
        "gstin": source.gstin, "grade": grade,
        "reliability": round(r, 3),
        "payments_observed": source.payments_observed,
        "avg_days_late": source.avg_days_late,
        "workers_affected": len(workers),
        "flagged_by_workers": source.flagged_by_workers or [],
        "note": "Scored across every worker on the platform - the signal one "
                "worker alone cannot see.",
    }


def statutory_exposure(*, amount: float, due_date: dt.date,
                       as_of: dt.date | None = None) -> dict:
    """For invoice-type income: what late payment costs the client. Gives an
    informal supplier real, quantified leverage they would never invoke alone."""
    as_of = as_of or dt.date.today()
    days_late = max(0, (as_of - due_date).days)
    annual = RBI_BANK_RATE * 3
    monthly = annual / 12
    interest = round(amount * ((1 + monthly) ** (days_late / 30.0) - 1), 2)
    fy_end = dt.date(as_of.year if as_of.month <= 3 else as_of.year + 1, 3, 31)
    at_risk = due_date < fy_end
    tax_cost = round(amount * CORPORATE_TAX_RATE, 2) if at_risk else 0.0
    return {"amount": round(amount, 2), "days_late": days_late,
            "msmed_interest_accrued": interest,
            "s43bh_tax_cost_if_unpaid": tax_cost,
            "total_exposure": round(interest + tax_cost, 2),
            "basis": "MSMED Act s.16 (3x RBI rate) + Income Tax s.43B(h)"}


def find_rings(db: Session, max_len: int = 6) -> list[dict]:
    """Cycle detection over the income graph (source -> worker as an earner
    who is also a source elsewhere). Fabricated-income rings show up as cycles."""
    graph: dict[str, set[str]] = {}
    names: dict[str, str] = {}
    for s in db.execute(select(IncomeSource)).scalars():
        names[s.id] = s.name
    for w in db.execute(select(Worker)).scalars():
        names[w.id] = w.name
    for e in db.execute(select(IncomeEvent)).scalars():
        if e.source_id and e.worker_id:
            graph.setdefault(e.source_id, set()).add(e.worker_id)

    rings, seen = [], set()

    def walk(start, node, path):
        if len(path) > max_len:
            return
        for nxt in graph.get(node, ()):
            if nxt == start and len(path) >= 2:
                key = frozenset(path)
                if key not in seen:
                    seen.add(key); rings.append(list(path))
            elif nxt not in path:
                walk(start, nxt, path + [nxt])

    for n in list(graph):
        walk(n, n, [n])
    return [{"parties": [names.get(p, p) for p in r], "length": len(r),
             "severity": "critical" if len(r) <= 3 else "warning"} for r in rings]
