"""Smart-allocation waterfall for a disbursed advance.

Obligations first (in priority order), then a savings sweep so resilience is
built even from borrowed smoothing, then the remainder is spendable. Routing
the money - rather than trusting it to be spent well - is what makes the credit
responsible and keeps the worker out of the next shortfall.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Advance, Allocation, Event, Obligation, Worker

CATEGORY_PRIORITY = {"rent": 1, "essential": 1, "emi": 2, "utility": 3,
                     "family": 4, "input": 2, "savings": 8, "other": 6}
LABEL = {"rent": "Rent", "essential": "Essentials", "emi": "Loan / EMI",
         "utility": "Utilities", "family": "Family support",
         "input": "Work inputs", "savings": "Resilience savings",
         "other": "Other", "spendable": "Spendable to worker"}


def plan(db: Session, worker: Worker, amount: float,
         savings_slice: float = 0.10) -> list[dict]:
    obligations = db.execute(select(Obligation).where(
        Obligation.worker_id == worker.id)).scalars().all()
    urgent = sorted((o for o in obligations
                     if o.category in ("rent", "essential", "emi", "utility")),
                    key=lambda o: (o.priority, CATEGORY_PRIORITY.get(o.category, 6)))

    legs: list[dict] = []
    remaining = round(amount, 2)

    # a small savings sweep off the top, so resilience grows even here
    sweep = round(amount * savings_slice, 2)
    if sweep >= 1:
        legs.append({"priority": 0, "payee": "Your resilience buffer",
                     "category": "savings", "amount": sweep})
        remaining = round(remaining - sweep, 2)

    for o in urgent:
        if remaining <= 0:
            break
        pay = round(min(o.monthly, remaining), 2)
        if pay <= 0:
            continue
        legs.append({"priority": CATEGORY_PRIORITY.get(o.category, 6),
                     "payee": o.payee or o.head, "category": o.category,
                     "head": o.head, "amount": pay})
        remaining = round(remaining - pay, 2)

    legs.append({"priority": 9, "payee": f"{worker.name} - UPI",
                 "category": "spendable", "amount": round(remaining, 2)})
    return sorted(legs, key=lambda l: l["priority"])


def commit(db: Session, advance: Advance, worker: Worker,
           savings_slice: float = 0.10) -> list[Allocation]:
    rows: list[Allocation] = []
    for spec in plan(db, worker, advance.net_disbursed, savings_slice):
        if spec["category"] == "savings":
            worker.savings_balance = round((worker.savings_balance or 0) + spec["amount"], 2)
        alloc = Allocation(advance_id=advance.id, worker_id=worker.id,
                           priority=spec["priority"], payee=spec["payee"],
                           category=spec["category"], amount=spec["amount"],
                           status="settled")
        db.add(alloc); rows.append(alloc)
        db.add(Event(type="allocation.settled", subject_id=advance.id,
                     payload={"payee": spec["payee"], "amount": spec["amount"],
                              "category": spec["category"]}))
    return rows
