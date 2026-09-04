"""Income volatility and the resilience score.

The defining feature of gig and informal work is not that income is low, it is
that it is *irregular*. A worker averaging the same monthly income as a
salaried peer can still be in constant crisis if that income arrives in
unpredictable lumps. So we measure the irregularity directly and turn it into
one number a worker - and a bank - can act on.

  volatility_index   coefficient of variation of weekly income (0 = a salary,
                     >0.6 = highly erratic). This is the honest measure of how
                     hard this person's cash flow is to manage.

  runway_days        how many days of essential spending the current buffer +
                     savings covers with no new income. The distress clock.

  resilience_score   0-100, combining runway, savings depth, income stability
                     and how well income covers obligations. One glanceable
                     number; the sub-scores are always shown alongside it so it
                     is never a black box.
"""
from __future__ import annotations

import datetime as dt
import statistics
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import IncomeEvent, Obligation, Worker

ESSENTIAL = {"essential", "rent", "utility", "food", "emi", "family"}


def _weekly_income(db: Session, worker_id: str, as_of: dt.date,
                   weeks: int = 12) -> list[float]:
    start = as_of - dt.timedelta(weeks=weeks)
    rows = db.execute(
        select(IncomeEvent).where(
            IncomeEvent.worker_id == worker_id,
            IncomeEvent.status.in_(("received", "advanced")))
    ).scalars().all()
    buckets: dict[int, float] = defaultdict(float)
    for e in rows:
        when = (e.received_date or e.expected_date)
        if not when:
            continue
        d = when.date() if isinstance(when, dt.datetime) else when
        if d < start or d > as_of:
            continue
        buckets[(d - start).days // 7] += e.gross
    return [buckets.get(i, 0.0) for i in range(weeks)]


def monthly_obligations(db: Session, worker_id: str) -> dict:
    rows = db.execute(select(Obligation).where(
        Obligation.worker_id == worker_id)).scalars().all()
    essential = sum(o.monthly for o in rows if o.category in ESSENTIAL)
    total = sum(o.monthly for o in rows)
    debt = sum(o.monthly for o in rows if o.is_debt)
    return {"total": round(total, 2), "essential": round(essential, 2),
            "debt_service": round(debt, 2),
            "lines": [{"head": o.head, "category": o.category,
                       "monthly": o.monthly, "is_debt": o.is_debt, "apr": o.apr}
                      for o in rows]}


def compute(db: Session, worker: Worker, as_of: dt.date | None = None) -> dict:
    as_of = as_of or dt.date.today()
    weekly = _weekly_income(db, worker.id, as_of)
    active = [w for w in weekly if w > 0] or [0.0]

    mean_w = statistics.mean(weekly) if weekly else 0.0
    sd_w = statistics.pstdev(weekly) if len(weekly) > 1 else 0.0
    volatility = round(sd_w / mean_w, 3) if mean_w else 0.0

    monthly_income = round(mean_w * 4.345, 2)
    obl = monthly_obligations(db, worker.id)
    daily_essential = obl["essential"] / 30.0 if obl["essential"] else 1.0

    buffer_total = (worker.cash_buffer or 0) + (worker.savings_balance or 0)
    runway_days = round(buffer_total / daily_essential, 1) if daily_essential else 0.0

    # income trend: last 4 weeks vs the prior 4
    recent = statistics.mean(weekly[-4:]) if len(weekly) >= 4 else mean_w
    prior = statistics.mean(weekly[-8:-4]) if len(weekly) >= 8 else mean_w
    trend = round((recent - prior) / prior, 3) if prior else 0.0

    coverage = round(monthly_income / obl["total"], 2) if obl["total"] else 2.0
    weeks_with_income = sum(1 for w in weekly if w > 0)

    # sub-scores, 0-100 each
    s_runway = min(100, runway_days / 30 * 100)              # 30 days = full
    s_savings = min(100, (worker.savings_balance or 0) /
                    max(obl["essential"], 1) * 100)          # 1 month saved = full
    s_stability = max(0, 100 - volatility * 120)             # cv 0.83 -> 0
    s_coverage = min(100, coverage / 1.3 * 100)
    resilience = round(0.35 * s_runway + 0.2 * s_savings +
                       0.25 * s_stability + 0.2 * s_coverage)

    band = ("critical" if resilience < 30 else "fragile" if resilience < 55
            else "building" if resilience < 75 else "resilient")

    return {
        "worker_id": worker.id,
        "as_of": as_of.isoformat(),
        "monthly_income_est": monthly_income,
        "weekly_income": [round(w, 2) for w in weekly],
        "volatility_index": volatility,
        "income_trend_4w": trend,
        "weeks_with_income": weeks_with_income,
        "runway_days": runway_days,
        "cash_buffer": round(worker.cash_buffer or 0, 2),
        "savings_balance": round(worker.savings_balance or 0, 2),
        "obligations": obl,
        "obligation_coverage": coverage,
        "resilience_score": resilience,
        "resilience_band": band,
        "sub_scores": {"runway": round(s_runway), "savings": round(s_savings),
                       "stability": round(s_stability), "coverage": round(s_coverage)},
    }
