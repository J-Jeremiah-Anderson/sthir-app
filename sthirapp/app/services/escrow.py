"""The escrow waterfall.

The lender's problem with unsecured working capital is diversion: money
advanced against a receivable that ends up somewhere other than the inputs
needed to fulfil the next order. A flat split does not solve that. A
priority waterfall does, because the rail - not the borrower's promise -
decides where each rupee lands.

Order is policy, not physics, and it is configurable. The default puts
statutory dues first (they compound and they are non-negotiable), then the
input supplier who gates production, then labour, then utilities, and only
the residual reaches the owner.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from ..models import Advance, EscrowLeg, Event, Mill

PRIORITY = {"statutory": 1, "input": 2, "wages": 3, "utility": 4, "owner": 5}

CATEGORY_LABEL = {
    "statutory": "Statutory dues",
    "input": "Input supplier",
    "wages": "Labour wages",
    "utility": "Utilities",
    "owner": "Owner drawdown",
}


def plan(mill: Mill, amount: float) -> list[dict]:
    """Allocate `amount` down the mill's obligations in priority order."""
    lines = sorted(
        (l for l in (mill.burn_lines or [])),
        key=lambda l: PRIORITY.get(l.get("category", "other"), 9))

    legs: list[dict] = []
    remaining = round(amount, 2)
    for line in lines:
        if remaining <= 0:
            break
        category = line.get("category", "other")
        if category == "owner":
            continue
        due = float(line.get("monthly", 0))
        pay = round(min(due, remaining), 2)
        if pay <= 0:
            continue
        legs.append({
            "priority": PRIORITY.get(category, 9),
            "payee": line.get("payee", line.get("head", "Supplier")),
            "category": category,
            "head": line.get("head"),
            "due": due,
            "amount": pay,
            "covered": round(pay / due, 3) if due else 1.0,
        })
        remaining = round(remaining - pay, 2)

    legs.append({
        "priority": PRIORITY["owner"],
        "payee": f"{mill.name} - operating account",
        "category": "owner",
        "head": "Owner drawdown",
        "due": remaining,
        "amount": round(remaining, 2),
        "covered": 1.0,
    })
    return legs


def commit(db: Session, advance: Advance, mill: Mill) -> list[EscrowLeg]:
    """Persist the legs and mark them paid through the mock payout rail."""
    rows: list[EscrowLeg] = []
    for spec in plan(mill, advance.net_disbursed):
        leg = EscrowLeg(
            advance_id=advance.id, priority=spec["priority"],
            payee=spec["payee"], category=spec["category"],
            amount=spec["amount"],
            account_ref=f"MOCK-{spec['category'][:3].upper()}-{advance.id[-6:]}",
            status="paid", paid_at=dt.datetime.now(dt.timezone.utc))
        db.add(leg)
        rows.append(leg)
        db.add(Event(type="escrow.leg_paid", subject_id=advance.id,
                     payload={"payee": spec["payee"], "amount": spec["amount"],
                              "category": spec["category"],
                              "priority": spec["priority"],
                              "rail": "mock"}))
    return rows
