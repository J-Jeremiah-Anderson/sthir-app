"""Early-warning engine (problem statement 3).

Distress is detected from the resilience snapshot before it becomes default:
a runway falling under a week, income trending sharply down, an expected
payout overdue, or debt service eating too much of income. Each signal maps to
a graded intervention - and, importantly, the intervention ladder tries the
cheapest, least indebting option first: a nudge, then the worker's own
savings, then a *responsible* advance, and only then external help.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import IncomeEvent, Worker

RUNWAY_CRITICAL = 7
RUNWAY_WATCH = 14
TREND_DROP = -0.20
DEBT_SERVICE_CAP = 0.30      # debt service > 30% of income is a spiral risk


def _overdue_payouts(db: Session, worker_id: str, as_of: dt.date) -> list[IncomeEvent]:
    rows = db.execute(select(IncomeEvent).where(
        IncomeEvent.worker_id == worker_id,
        IncomeEvent.status == "expected")).scalars().all()
    return [e for e in rows if e.expected_date and e.expected_date.date() < as_of]


def assess(db: Session, worker: Worker, snapshot: dict,
           as_of: dt.date | None = None) -> list[dict]:
    as_of = as_of or dt.date.today()
    alerts: list[dict] = []

    runway = snapshot["runway_days"]
    if runway <= RUNWAY_CRITICAL:
        alerts.append({
            "severity": "critical", "code": "runway_low",
            "title": f"Only {runway:.0f} days of essentials covered",
            "detail": ("Cash buffer plus savings covers essential spending for "
                       f"{runway:.0f} days. This is the point where workers turn "
                       "to high-interest informal lenders."),
            "signals": [f"runway {runway:.0f}d", f"buffer Rs {snapshot['cash_buffer']:,.0f}"],
        })
    elif runway <= RUNWAY_WATCH:
        alerts.append({
            "severity": "warning", "code": "runway_low",
            "title": f"Runway down to {runway:.0f} days",
            "detail": "Buffer is thinning. Act now, while cheap options still exist.",
            "signals": [f"runway {runway:.0f}d"],
        })

    trend = snapshot["income_trend_4w"]
    if trend <= TREND_DROP:
        alerts.append({
            "severity": "warning", "code": "income_drop",
            "title": f"Income down {abs(trend):.0%} over the last month",
            "detail": ("Recent weekly income is well below the prior month. A "
                       "sustained drop is the earliest sign of trouble."),
            "signals": [f"4-week trend {trend:+.0%}"],
        })

    overdue = _overdue_payouts(db, worker.id, as_of)
    if overdue:
        total = sum(e.gross for e in overdue)
        alerts.append({
            "severity": "warning", "code": "payout_overdue",
            "title": f"Rs {total:,.0f} in expected income is late",
            "detail": (f"{len(overdue)} expected payment(s) have not arrived. "
                       "Money the worker is counting on is at risk."),
            "signals": [f"{e.reference or e.kind}: Rs {e.gross:,.0f} "
                        f"(due {e.expected_date.date()})" for e in overdue],
        })

    obl = snapshot["obligations"]
    income = snapshot["monthly_income_est"] or 1
    debt_share = obl["debt_service"] / income if income else 0
    moneylender = [l for l in obl["lines"] if l["is_debt"] and l["apr"] > 0.30]
    # A predatory-rate borrowing is itself the red flag, even below the ratio cap.
    if debt_share > DEBT_SERVICE_CAP or moneylender:
        alerts.append({
            "severity": "critical" if moneylender else "warning",
            "code": "debt_spiral",
            "title": f"Debt service is {debt_share:.0%} of income",
            "detail": ("A large share of income is going to repay existing debt"
                       + (f", including borrowing at {max(l['apr'] for l in moneylender):.0%} "
                          "APR - a classic debt trap." if moneylender else ".")),
            "signals": [f"{l['head']} @ {l['apr']:.0%} APR" for l in moneylender]
                       or [f"debt service Rs {obl['debt_service']:,.0f}/mo"],
        })

    for a in alerts:
        a["intervention"] = _intervention(a["code"], worker, snapshot)
    return alerts


def _intervention(code: str, worker: Worker, snapshot: dict) -> dict:
    """The graded response. Cheapest, least-indebting option first."""
    savings = worker.savings_balance or 0
    essential_daily = (snapshot["obligations"]["essential"] / 30.0) or 1

    ladder = []
    if code in ("runway_low", "income_drop", "payout_overdue"):
        if savings > 0:
            days = round(savings / essential_daily, 1)
            ladder.append({"step": "draw_savings", "label": "Draw your resilience buffer first",
                           "detail": f"Rs {savings:,.0f} of your own savings covers "
                                     f"about {days:.0f} more days at zero cost."})
        ladder.append({"step": "smoothing_advance",
                       "label": "Responsible advance against verified upcoming income",
                       "detail": "Only if affordable after repayment - the engine "
                                 "refuses it if it would worsen your position."})
    if code == "debt_spiral":
        ladder.append({"step": "refinance",
                       "label": "Replace high-interest informal debt",
                       "detail": "Consolidate moneylender debt into a lower-cost, "
                                 "transparent facility."})
        ladder.append({"step": "connect_scheme",
                       "label": "Connect to a government support scheme",
                       "detail": "e-Shram / PM-SYM eligibility check for informal workers."})
    ladder.append({"step": "nudge", "label": "Guidance",
                   "detail": "Plain-language explanation of what changed and why, "
                             "in the worker's own language."})
    return {"ladder": ladder}
