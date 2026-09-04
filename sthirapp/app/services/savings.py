"""Auto-savings: sweep on good weeks, draw down on lean ones.

Resilience is built on the good weeks, not spent on them. When a worker's
income week is above their own recent median, a small configurable slice is
swept into a locked buffer. On lean weeks the buffer is drawn down *before*
any credit is offered - so the worker's own money always beats borrowing.
"""
from __future__ import annotations

import statistics

from sqlalchemy.orm import Session

from ..models import Event, SavingsTxn, Worker


def sweep(db: Session, worker: Worker, income_this_week: float,
          recent_weeks: list[float]) -> dict | None:
    active = [w for w in recent_weeks if w > 0]
    if not active:
        return None
    median = statistics.median(active)
    if income_this_week <= median:
        return None
    surplus = income_this_week - median
    amount = round(surplus * (worker.autosave_rate or 0.10), 2)
    if amount < 1:
        return None
    worker.savings_balance = round((worker.savings_balance or 0) + amount, 2)
    txn = SavingsTxn(worker_id=worker.id, direction="sweep_in", amount=amount,
                     reason=f"above-median week (+Rs {surplus:,.0f})",
                     balance_after=worker.savings_balance)
    db.add(txn)
    db.add(Event(type="savings.sweep_in", subject_id=worker.id,
                 payload={"amount": amount, "balance": worker.savings_balance}))
    return {"amount": amount, "balance": worker.savings_balance}


def draw_down(db: Session, worker: Worker, need: float) -> dict:
    available = worker.savings_balance or 0
    drawn = round(min(available, need), 2)
    if drawn <= 0:
        return {"drawn": 0.0, "balance": available, "shortfall": round(need, 2)}
    worker.savings_balance = round(available - drawn, 2)
    worker.cash_buffer = round((worker.cash_buffer or 0) + drawn, 2)
    db.add(SavingsTxn(worker_id=worker.id, direction="draw_down", amount=drawn,
                      reason="lean-week drawdown before credit",
                      balance_after=worker.savings_balance))
    db.add(Event(type="savings.draw_down", subject_id=worker.id,
                 payload={"amount": drawn, "balance": worker.savings_balance}))
    return {"drawn": drawn, "balance": worker.savings_balance,
            "shortfall": round(max(0, need - drawn), 2)}
