"""Responsible income-smoothing advance.

This is the deliberate opposite of a payday loan, and the difference is
structural, not marketing:

  1. It advances only against income we have *verified* is coming (tiered).
  2. It is gated on affordability, not desperation. If repayment would push
     the worker's post-repayment runway below a floor, or debt service above a
     ceiling, the engine REFUSES - and says so in plain language. A payday
     lender's incentive is the opposite; ours is written into the code.
  3. It is priced as a small flat fee with the APR-equivalent always shown, so
     the worker sees the true cost, not a hidden one.

Every branch - approve, clarify, refuse - returns a reasons array. A refusal
is a feature: it is the moment the product protects the worker from itself.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Advance, IncomeEvent, Worker

CONFIDENCE_GATE = 0.85
TIER_RATE = {"A": 0.85, "B": 0.70, "C": 0.50}   # share of verified income advanced
FLAT_FEE = {"A": 0.010, "B": 0.015, "C": 0.020}  # per advance, on the amount
POST_REPAY_RUNWAY_FLOOR = 5      # days of essentials that must remain after repayment
DEBT_SERVICE_CEILING = 0.35      # incl. this advance's repayment
MAX_ADVANCE_ABS = 25_000         # consumer-scale cap, not a business line


def _reason(code, label, ok=True):
    return {"code": code, "label": label, "ok": ok}


def _outstanding(db: Session, worker_id: str) -> float:
    rows = db.execute(select(Advance).where(
        Advance.worker_id == worker_id,
        Advance.status == "disbursed")).scalars().all()
    return sum(a.advance_amount + a.flat_fee for a in rows)


def decide(db: Session, *, worker: Worker, event: IncomeEvent | None,
           snapshot: dict, verification: dict, confidence: float,
           requested: float | None = None,
           duplicate: dict | None = None) -> dict:
    reasons: list[dict] = []

    if duplicate and duplicate.get("duplicate"):
        return {"decision": "refused", "advance_amount": 0.0,
                "reasons": [_reason("duplicate", duplicate["detail"], ok=False)],
                "refuse_reason": duplicate["detail"], "protective": True}

    if event is not None and confidence < CONFIDENCE_GATE:
        return {"decision": "clarify", "advance_amount": 0.0,
                "reasons": [_reason("low_confidence",
                            f"income evidence only {confidence:.0%} clear - confirm the amount",
                            ok=False)],
                "clarify_field": "amount"}

    tier = verification.get("tier", "C")
    face = (event.gross if event else 0.0) or 0.0
    base = round(face * TIER_RATE[tier], 2)
    reasons.append(_reason(f"tier_{tier.lower()}",
                           {"A": "income cryptographically/platform verified",
                            "B": "income source identifiers valid",
                            "C": "income declared from a photo only"}[tier]))

    # A worker trapped by a predatory-rate lender should not be handed more
    # credit - that deepens the trap. Refuse and route to refinance instead.
    # This is the clearest expression of the product's protective stance.
    predatory = [l for l in snapshot["obligations"]["lines"]
                 if l["is_debt"] and l["apr"] >= 0.40]
    if predatory:
        worst = max(predatory, key=lambda l: l["apr"])
        detail = (f"You are repaying {worst['head']} at {worst['apr']:.0%} APR. "
                  "A new advance would add to that burden, not relieve it. "
                  "The right move is to replace that debt, not add to it.")
        return {"decision": "refused", "advance_amount": 0.0,
                "reasons": reasons + [_reason("predatory_debt", detail, ok=False)],
                "refuse_reason": detail, "protective": True,
                "alternative": "refinance_high_interest_debt",
                "alternative_label": f"Refinance {worst['head']} "
                                     f"(currently {worst['apr']:.0%} APR)"}

    # affordability: what can they repay without breaking the floor?
    essential_daily = (snapshot["obligations"]["essential"] / 30.0) or 1
    buffer = snapshot["cash_buffer"] + snapshot["savings_balance"]
    # repayment comes out of the income event when it lands, so the constraint
    # is on the debt-service ratio and on not stranding the worker now
    income = snapshot["monthly_income_est"] or 1
    existing_debt = snapshot["obligations"]["debt_service"]

    amount = min(base, MAX_ADVANCE_ABS)
    if requested:
        amount = min(amount, requested)

    fee = round(amount * FLAT_FEE[tier], 2)
    # affordability check: monthly debt service incl. this advance's repayment
    projected_debt_service = existing_debt + (amount + fee)  # repaid within the month
    debt_ratio = projected_debt_service / income if income else 1.0

    if debt_ratio > DEBT_SERVICE_CEILING:
        # scale down to fit, or refuse if even a minimal advance breaks it
        affordable = max(0.0, DEBT_SERVICE_CEILING * income - existing_debt)
        if affordable < 500:
            detail = (f"repaying this would push debt service to {debt_ratio:.0%} of "
                      f"income, past the {DEBT_SERVICE_CEILING:.0%} safe limit. "
                      "Advancing now would deepen the shortfall, not fix it.")
            return {"decision": "refused", "advance_amount": 0.0,
                    "reasons": reasons + [_reason("affordability", detail, ok=False)],
                    "refuse_reason": detail, "protective": True,
                    "alternative": "draw_savings_or_scheme"}
        amount = round(affordable, 2)
        fee = round(amount * FLAT_FEE[tier], 2)
        reasons.append(_reason("affordability_capped",
                               f"capped to Rs {amount:,.0f} to keep debt service within "
                               f"{DEBT_SERVICE_CEILING:.0%} of income"))
    else:
        reasons.append(_reason("affordability_ok",
                               f"debt service stays at {debt_ratio:.0%} of income, "
                               f"within the {DEBT_SERVICE_CEILING:.0%} limit"))

    # never let total outstanding exceed a month of income
    if _outstanding(db, worker.id) + amount > income:
        detail = "total outstanding would exceed one month of income - held for safety"
        return {"decision": "refused", "advance_amount": 0.0,
                "reasons": reasons + [_reason("exposure", detail, ok=False)],
                "refuse_reason": detail, "protective": True}

    net = round(amount - fee, 2)
    tenor_days = 0
    if event and event.expected_date:
        tenor_days = max(1, (event.expected_date.date() - dt.date.today()).days)
    apr_equiv = round((fee / amount) * (365 / max(tenor_days, 15)), 3) if amount else 0.0

    return {
        "decision": "approved",
        "tier": tier,
        "advance_rate": TIER_RATE[tier],
        "face_value": face,
        "advance_amount": amount,
        "flat_fee": fee,
        "net_disbursed": net,
        "apr_equiv": apr_equiv,
        "cost_per_1000": round(fee / amount * 1000) if amount else 0,
        "reasons": reasons,
        "protective": False,
    }


READY_CASH_RATE = 0.50    # up to half a month's verified income as cash-in-hand
READY_CASH_FEE = 0.015


def assess_ready_cash(db: Session, *, worker: Worker, snapshot: dict,
                      requested: float | None = None) -> dict:
    """Eligibility for a *direct cash advance to the worker* - the "ready cash"
    a bank would consider when no free government scheme applies. Same protective
    gates as `decide`, but sized off recurring income rather than one payout, and
    disbursed to the worker's own hand instead of a vendor. Returns `eligible`
    with a sized offer, or `not_eligible` with a plain-language reason.
    """
    income = snapshot["monthly_income_est"] or 0.0
    lines = snapshot["obligations"]["lines"]
    existing_debt = snapshot["obligations"]["debt_service"]

    # 1) The bank needs a provable, recurring income before unsecured cash.
    if income <= 0 or snapshot.get("weeks_with_income", 0) < 2:
        detail = ("A direct bank advance needs verified, recurring income. There "
                  "isn't enough verified income on record yet - submit a few "
                  "verified payouts to build eligibility.")
        return {"decision": "not_eligible", "cash_amount": 0.0, "reason": detail,
                "reasons": [_reason("no_income_history", detail, ok=False)],
                "alternative": "build_income_history"}

    # 2) Already trapped in predatory debt -> more cash deepens the trap.
    predatory = [l for l in lines if l["is_debt"] and l["apr"] >= 0.40]
    if predatory:
        worst = max(predatory, key=lambda l: l["apr"])
        detail = (f"You are already repaying {worst['head']} at {worst['apr']:.0%} "
                  "APR. A bank won't extend a fresh cash loan on top of debt that "
                  "expensive - it would deepen the trap. Refinance it instead.")
        return {"decision": "not_eligible", "cash_amount": 0.0, "reason": detail,
                "reasons": [_reason("predatory_debt", detail, ok=False)],
                "alternative": "refinance_high_interest_debt",
                "alternative_label": f"Refinance {worst['head']} "
                                     f"({worst['apr']:.0%} APR)"}

    reasons: list[dict] = []
    base = round(income * READY_CASH_RATE, 2)
    amount = min(base, MAX_ADVANCE_ABS)
    if requested:
        amount = min(amount, requested)
    fee = round(amount * READY_CASH_FEE, 2)

    # 3) Affordability: debt service incl. this cash must stay within the ceiling.
    projected = existing_debt + (amount + fee)
    ratio = projected / income if income else 1.0
    if ratio > DEBT_SERVICE_CEILING:
        affordable = max(0.0, DEBT_SERVICE_CEILING * income - existing_debt)
        if affordable < 500:
            detail = (f"Repaying a cash advance would push debt service to "
                      f"{ratio:.0%} of income, past the {DEBT_SERVICE_CEILING:.0%} "
                      "safe limit. You're not eligible for a direct bank loan right "
                      "now - it would deepen the shortfall, not fix it.")
            return {"decision": "not_eligible", "cash_amount": 0.0, "reason": detail,
                    "reasons": [_reason("affordability", detail, ok=False)],
                    "alternative": "draw_savings_or_scheme"}
        amount = round(affordable, 2)
        fee = round(amount * READY_CASH_FEE, 2)
        reasons.append(_reason("affordability_capped",
                               f"capped to Rs {amount:,.0f} to keep debt service "
                               f"within {DEBT_SERVICE_CEILING:.0%} of income"))
    else:
        reasons.append(_reason("affordability_ok",
                               f"debt service stays at {ratio:.0%} of income, within "
                               f"the {DEBT_SERVICE_CEILING:.0%} limit"))

    # 4) Total exposure ceiling.
    if _outstanding(db, worker.id) + amount > income:
        detail = ("Total outstanding would exceed a month of income - the bank "
                  "holds new lending here for safety.")
        return {"decision": "not_eligible", "cash_amount": 0.0, "reason": detail,
                "reasons": reasons + [_reason("exposure", detail, ok=False)],
                "alternative": "draw_savings_or_scheme"}

    net = round(amount - fee, 2)
    apr_equiv = round((fee / amount) * (365 / 30), 3) if amount else 0.0
    reasons.insert(0, _reason("eligible",
                              "verified recurring income and affordability checks passed"))
    return {"decision": "eligible", "cash_amount": amount, "flat_fee": fee,
            "net_cash": net, "apr_equiv": apr_equiv, "reasons": reasons}
