"""Runway, in days, with the counterfactual.

Rupees are an abstraction to somebody deciding whether to run the looms next
week. Days are not. The number that matters is not "you received 3.7 lakh",
it is "you can pay everyone for 47 days instead of 11".
"""
from __future__ import annotations

from ..models import Mill


def compute(mill: Mill, injection: float = 0.0) -> dict:
    burn_lines = mill.burn_lines or []
    monthly = float(mill.monthly_burn or sum(l.get("monthly", 0) for l in burn_lines))
    daily = monthly / 30.0 if monthly else 0.0
    cash = float(mill.cash_on_hand or 0.0)

    def days(x: float) -> float:
        return round(x / daily, 1) if daily else 0.0

    return {
        "mill_id": mill.id,
        "mill_name": mill.name,
        "cash_on_hand": round(cash, 2),
        "monthly_burn": round(monthly, 2),
        "daily_burn": round(daily, 2),
        "days_without_advance": days(cash),
        "days_with_advance": days(cash + injection),
        "delta_days": round(days(cash + injection) - days(cash), 1),
        "injection": round(injection, 2),
        "burn_lines": [
            {**l, "daily": round(l.get("monthly", 0) / 30.0, 2),
             "share": round(l.get("monthly", 0) / monthly, 3) if monthly else 0}
            for l in burn_lines
        ],
    }
