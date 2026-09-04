"""The credit decision.

Two things separate this from the weighted-sum score it replaces.

First, extraction confidence is a *gate*, not a term. Low confidence is a
statement about our pipeline's uncertainty, not about the borrower's
reliability; adding it to a credit score conflates the two. Below the gate we
do not lend less - we ask one targeted question.

Second, how much we advance and what we charge are decided separately, the
way receivables finance actually works. The advance rate is about how much of
the face value we are willing to have outstanding; the discount rate is the
price of the money for the days it is outstanding.

Every adjustment appends to `reasons`, and that stored array is the
explainability feature - never show a bare score.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import (ADVANCE_CAP, ADVANCE_FLOOR, BASE_ADVANCE_RATE, BOOK_SIZE,
                      CONFIDENCE_GATE, COST_OF_FUNDS_30D,
                      MAX_EXPOSURE_PER_BUYER, MAX_EXPOSURE_PER_MILL)
from ..models import Advance, Buyer, Invoice

TIER_DELTA = {"A": 0.08, "B": 0.00, "C": -0.15}
TIER_SPREAD = {"A": 0.004, "B": 0.009, "C": 0.016}
CONCENTRATION_FREE = 0.40      # share of a mill's book allowed in one buyer
CONCENTRATION_SLOPE = 0.30
TENOR_FREE_DAYS = 60


def _reason(code, label, delta=0.0):
    return {"code": code, "label": label, "delta": round(delta, 4)}


def _outstanding(db: Session, *, mill_id=None, buyer_id=None) -> float:
    stmt = (select(Advance, Invoice)
            .join(Invoice, Advance.invoice_id == Invoice.id)
            .where(Advance.status == "disbursed"))
    if mill_id:
        stmt = stmt.where(Invoice.mill_id == mill_id)
    if buyer_id:
        stmt = stmt.where(Invoice.buyer_id == buyer_id)
    return sum(a.advance_amount for a, _ in db.execute(stmt).all())


def buyer_track_record(buyer: Buyer | None) -> tuple[float, str]:
    if buyer is None:
        return 0.0, "buyer not identified"
    total = buyer.invoices_settled + buyer.invoices_late
    if total == 0:
        return 0.0, "new buyer, no settlement history"
    rate = buyer.on_time_rate
    if rate >= 0.99 and buyer.invoices_settled >= 3:
        return 0.05, f"{buyer.invoices_settled} prior invoices, all settled on time"
    if rate >= 0.80:
        return 0.02, f"{rate:.0%} on-time across {total} prior invoices"
    if rate < 0.60:
        return -0.10, (f"only {rate:.0%} on-time across {total} prior invoices, "
                       f"averaging {buyer.avg_days_late:.0f} days late")
    return -0.04, (f"{rate:.0%} on-time across {total} prior invoices, "
                   f"averaging {buyer.avg_days_late:.0f} days late")


def decide(db: Session, *, invoice: Invoice, buyer: Buyer | None,
           verification: dict, confidence: float,
           duplicate: dict | None = None,
           as_of: dt.date | None = None) -> dict:
    as_of = as_of or dt.date.today()
    reasons: list[dict] = []

    # ---- hard rejects, checked before any scoring ----
    if duplicate and duplicate.get("duplicate"):
        return {"decision": "rejected", "advance_rate": 0.0, "advance_amount": 0.0,
                "reasons": [_reason("duplicate", duplicate["detail"])],
                "reject_reason": duplicate["detail"],
                "reject_code": "double_financing"}

    critical = [m for m in verification["mismatches"] if m["severity"] == "critical"]
    if critical:
        detail = critical[0]["detail"]
        return {"decision": "rejected", "advance_rate": 0.0, "advance_amount": 0.0,
                "reasons": [_reason("integrity", detail)],
                "reject_reason": detail, "reject_code": "integrity_failure"}

    # ---- the gate: uncertainty is not risk appetite ----
    if confidence < CONFIDENCE_GATE:
        return {"decision": "clarify", "advance_rate": 0.0, "advance_amount": 0.0,
                "reasons": [_reason("low_confidence",
                                    f"extraction confidence {confidence:.0%} is below "
                                    f"the {CONFIDENCE_GATE:.0%} gate")],
                "clarify_field": "total_amount",
                "reject_reason": None, "reject_code": None}

    # ---- advance rate ----
    rate = BASE_ADVANCE_RATE
    reasons.append(_reason("base", f"base advance rate {BASE_ADVANCE_RATE:.0%}",
                           BASE_ADVANCE_RATE))

    tier = verification["tier"]
    d = TIER_DELTA[tier]
    label = {"A": "IRP signature verified against the printed invoice",
             "B": "GSTIN checksum valid, no signed QR on the document",
             "C": "no cryptographic or registry verification available"}[tier]
    rate += d
    reasons.append(_reason(f"tier_{tier.lower()}", label, d))

    d, label = buyer_track_record(buyer)
    rate += d
    reasons.append(_reason("track_record", label, d))

    # concentration, measured on the mill's own funded book
    mill_book = _outstanding(db, mill_id=invoice.mill_id)
    buyer_book = _outstanding(db, mill_id=invoice.mill_id, buyer_id=invoice.buyer_id)
    prospective = invoice.amount * rate
    share = ((buyer_book + prospective) / (mill_book + prospective)
             if (mill_book + prospective) > 0 else 0.0)
    if share > CONCENTRATION_FREE:
        pen = (share - CONCENTRATION_FREE) * CONCENTRATION_SLOPE
        rate -= pen
        reasons.append(_reason("concentration",
                               f"this buyer would be {share:.0%} of the mill's funded "
                               f"book, above the {CONCENTRATION_FREE:.0%} threshold",
                               -pen))

    days_to_due = max(0, (invoice.due_date.date() - as_of).days
                      if invoice.due_date else 60)
    if days_to_due > TENOR_FREE_DAYS:
        pen = (days_to_due - TENOR_FREE_DAYS) / 1000.0
        rate -= pen
        reasons.append(_reason("tenor",
                               f"{days_to_due} days to due date, {days_to_due - TENOR_FREE_DAYS} "
                               f"beyond the {TENOR_FREE_DAYS}-day threshold", -pen))

    rate = max(ADVANCE_FLOOR, min(ADVANCE_CAP, rate))
    advance_amount = round(invoice.amount * rate, 2)

    # ---- exposure limits, independent of score ----
    if _outstanding(db, buyer_id=invoice.buyer_id) + advance_amount > MAX_EXPOSURE_PER_BUYER * BOOK_SIZE:
        detail = (f"would breach the per-buyer exposure limit of "
                  f"{MAX_EXPOSURE_PER_BUYER:.0%} of the book")
        return {"decision": "rejected", "advance_rate": rate, "advance_amount": 0.0,
                "reasons": reasons + [_reason("exposure_buyer", detail)],
                "reject_reason": detail, "reject_code": "exposure_limit"}
    if mill_book + advance_amount > MAX_EXPOSURE_PER_MILL * BOOK_SIZE:
        detail = (f"would breach the per-mill exposure limit of "
                  f"{MAX_EXPOSURE_PER_MILL:.0%} of the book")
        return {"decision": "rejected", "advance_rate": rate, "advance_amount": 0.0,
                "reasons": reasons + [_reason("exposure_mill", detail)],
                "reject_reason": detail, "reject_code": "exposure_limit"}

    # ---- price ----
    spread = TIER_SPREAD[tier]
    if buyer and buyer.invoices_late and buyer.on_time_rate < 0.8:
        spread += 0.004
    discount_30d = COST_OF_FUNDS_30D + spread
    months = max(days_to_due, 1) / 30.0
    fee = round(advance_amount * discount_30d * months, 2)

    return {
        "decision": "approved",
        "advance_rate": round(rate, 4),
        "advance_amount": advance_amount,
        "face_value": invoice.amount,
        "discount_rate_30d": round(discount_30d, 5),
        "fee": fee,
        "net_disbursed": round(advance_amount - fee, 2),
        "days_to_due": days_to_due,
        "rupees_per_lakh_per_month": round(discount_30d * 100_000),
        "reasons": reasons,
        "reject_reason": None,
        "reject_code": None,
    }
