"""Buyer bureau: statutory leverage, payment scoring, and fraud-ring detection.

Three capabilities that only exist because we sit on the invoice flow:

1. Statutory exposure. Two Indian provisions bite a buyer who pays a micro
   or small supplier late, and almost no MSME ever invokes either, because
   enforcing means losing the customer:

     - MSMED Act s.16: interest at three times the RBI bank rate, compounded
       with monthly rests, running from the appointed day. It is statutory
       and overrides whatever the contract says.
     - Income Tax Act s.43B(h) (Finance Act 2023, in force from AY 2024-25):
       the buyer may deduct the expense only in the year it is actually
       paid. Still unpaid on 31 March and the amount is added back to their
       taxable income.

   Once the receivable is assigned to us we are the creditor, and we have no
   commercial relationship to protect. Quantifying the buyer's exposure turns
   collections from a favour into arithmetic.

2. Payment scoring. Credit bureaus rate borrowers. Nobody rates how a
   mid-market buyer actually pays its small suppliers, because nobody holds
   the data. We do, across every mill on the platform.

3. Ring detection. The sophisticated invoice fraud is not a duplicate, it is
   a cycle: A bills B, B bills C, C bills A, none of it real, all of it
   financed. That is a cycle-detection problem on a directed graph.
"""
from __future__ import annotations

import datetime as dt
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Buyer, Invoice, Mill

# RBI bank rate. Configurable because it moves.
RBI_BANK_RATE = float(os.getenv("FF_RBI_BANK_RATE", "0.0600"))
MSMED_MULTIPLE = 3
APPOINTED_DAY_DAYS = 45      # with a written agreement; 15 without
CORPORATE_TAX_RATE = float(os.getenv("FF_CORPORATE_TAX_RATE", "0.25"))


def statutory_exposure(*, amount: float, due_date: dt.date,
                       as_of: dt.date | None = None,
                       written_agreement: bool = True) -> dict:
    """What late payment on this invoice costs the buyer, in their own money."""
    as_of = as_of or dt.date.today()
    window = APPOINTED_DAY_DAYS if written_agreement else 15
    appointed = due_date if due_date else as_of
    days_late = max(0, (as_of - appointed).days)

    annual = RBI_BANK_RATE * MSMED_MULTIPLE           # e.g. 18% p.a.
    monthly = annual / 12
    months = days_late / 30.0
    interest = round(amount * ((1 + monthly) ** months - 1), 2)

    # 43B(h): unpaid at the financial year end means the deduction is lost
    # for that year. India's FY ends 31 March.
    fy_end = dt.date(as_of.year if as_of.month <= 3 else as_of.year + 1, 3, 31)
    at_risk = appointed < fy_end
    tax_cost = round(amount * CORPORATE_TAX_RATE, 2) if at_risk else 0.0

    return {
        "amount": round(amount, 2),
        "appointed_day": appointed.isoformat(),
        "payment_window_days": window,
        "days_late": days_late,
        "msmed_annual_rate": round(annual, 4),
        "msmed_interest_accrued": interest,
        "s43bh_deduction_at_risk": at_risk,
        "s43bh_tax_cost_if_unpaid_at_fy_end": tax_cost,
        "fy_end": fy_end.isoformat(),
        "total_exposure": round(interest + tax_cost, 2),
        "basis": ("MSMED Act s.16 (3x RBI bank rate, monthly rests) and "
                  "Income Tax Act s.43B(h)"),
    }


def buyer_notice(buyer_name: str, invoice_number: str, exposure: dict) -> str:
    """The text we send the buyer. Factual, quantified, no threats."""
    lines = [
        f"Payment notice - invoice {invoice_number}",
        f"To: {buyer_name}",
        "",
        f"Amount outstanding: Rs {exposure['amount']:,.0f}",
        f"Due on: {exposure['appointed_day']}",
    ]
    if exposure["days_late"] > 0:
        lines += [
            f"Days beyond the appointed day: {exposure['days_late']}",
            "",
            f"Interest accrued under MSMED Act s.16 "
            f"({exposure['msmed_annual_rate']:.0%} p.a., compounded monthly): "
            f"Rs {exposure['msmed_interest_accrued']:,.0f}",
        ]
    else:
        lines += ["", "This invoice is not yet overdue. This notice sets out the "
                      "consequences of late payment in advance."]
    if exposure["s43bh_deduction_at_risk"]:
        lines += [
            "",
            f"Under s.43B(h) of the Income Tax Act, if this amount is unpaid on "
            f"{exposure['fy_end']} the expense is not deductible in this financial "
            f"year. At a {CORPORATE_TAX_RATE:.0%} rate that is approximately "
            f"Rs {exposure['s43bh_tax_cost_if_unpaid_at_fy_end']:,.0f} in "
            f"additional tax.",
        ]
    lines += ["", "This receivable has been assigned to FabricFund. "
                  "Please remit to the virtual account on the invoice."]
    return "\n".join(lines)


# --------------------------------------------------------------- scoring

GRADES = [(0.95, "A"), (0.85, "B"), (0.70, "C"), (0.50, "D")]


def scorecard(db: Session, buyer: Buyer) -> dict:
    total = buyer.invoices_settled + buyer.invoices_late
    on_time = buyer.on_time_rate
    grade = next((g for threshold, g in GRADES if on_time >= threshold), "E")
    if total == 0:
        grade = "-"

    funded = db.execute(
        select(Invoice).where(Invoice.buyer_id == buyer.id,
                              Invoice.status.in_(("funded", "settled")))
    ).scalars().all()
    suppliers = len({i.mill_id for i in funded})

    return {
        "buyer_id": buyer.id,
        "name": buyer.name,
        "gstin": buyer.gstin,
        "turnover_band": buyer.turnover_band,
        "treds_eligible": buyer.treds_eligible,
        "grade": grade,
        "on_time_rate": round(on_time, 3),
        "invoices_observed": total,
        "invoices_late": buyer.invoices_late,
        "avg_days_late": buyer.avg_days_late,
        "suppliers_observed": suppliers,
        "flagged_by_mills": buyer.flagged_by_mills or [],
        "note": ("Observed across every mill on the platform, not just one "
                 "relationship - this is the signal a single supplier cannot see."),
    }


# --------------------------------------------------------- ring detection

def _edges(db: Session) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Directed edges buyer -> seller, keyed by GSTIN. Money flows along them."""
    graph: dict[str, set[str]] = {}
    names: dict[str, str] = {}

    mills = {m.id: m for m in db.execute(select(Mill)).scalars().all()}
    buyers = {b.id: b for b in db.execute(select(Buyer)).scalars().all()}
    for m in mills.values():
        names[m.gstin] = m.name
    for b in buyers.values():
        names[b.gstin] = b.name

    for inv in db.execute(select(Invoice)).scalars().all():
        mill = mills.get(inv.mill_id)
        buyer = buyers.get(inv.buyer_id)
        if not mill or not buyer:
            continue
        graph.setdefault(buyer.gstin, set()).add(mill.gstin)
    return graph, names


def find_rings(db: Session, max_len: int = 6) -> list[dict]:
    """Every simple cycle in the invoice graph.

    A cycle means party A ultimately bills itself through a chain of others.
    In legitimate trade this is rare and short-lived; in invoice fraud it is
    the standard structure.
    """
    graph, names = _edges(db)
    rings: list[list[str]] = []
    seen: set[frozenset[str]] = set()

    def walk(start: str, node: str, path: list[str]):
        if len(path) > max_len:
            return
        for nxt in graph.get(node, ()):
            if nxt == start and len(path) >= 2:
                key = frozenset(path)
                if key not in seen:
                    seen.add(key)
                    rings.append(list(path))
            elif nxt not in path:
                walk(start, nxt, path + [nxt])

    for node in list(graph):
        walk(node, node, [node])

    return [{
        "gstins": r,
        "parties": [names.get(g, g) for g in r],
        "length": len(r),
        "severity": "critical" if len(r) <= 3 else "warning",
        "detail": (" -> ".join(names.get(g, g) for g in r) +
                   f" -> {names.get(r[0], r[0])}"),
    } for r in rings]
