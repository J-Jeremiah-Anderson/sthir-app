"""Orchestration - every surface calls these two functions.

process_income()   verify a submitted income event and record it, then return
                   the worker's refreshed resilience snapshot and any alerts.
request_advance()  run the responsible-credit decision on a verified income
                   event; on approval, disburse and allocate.

Both write to the append-only event log so the demo replays from clean.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (Advance, Event, IncomeEvent, IncomeSource, Obligation,
                      Worker)
from . import allocation, credit, distress, resilience, sources
from .extract import extract
from .income_verify import verify as verify_income
from .verify import fuzzy_key


def _known_gstins(db: Session) -> set[str]:
    return {s.gstin for s in db.execute(select(IncomeSource)).scalars() if s.gstin}


def snapshot_and_alerts(db: Session, worker: Worker, as_of: dt.date) -> dict:
    snap = resilience.compute(db, worker, as_of)
    alerts = distress.assess(db, worker, snap, as_of)
    return {"resilience": snap, "alerts": alerts}


def process_income(db: Session, event: IncomeEvent, *,
                   as_of: dt.date | None = None, commit: bool = True,
                   precomputed=None) -> dict:
    as_of = as_of or dt.date.today()
    worker = db.get(Worker, event.worker_id)
    source = db.get(IncomeSource, event.source_id) if event.source_id else None

    extraction = None
    if precomputed is not None:
        # endpoint already extracted (and gated) - reuse, don't call the model twice
        extraction, backend = precomputed
        if extraction is not None:
            event.confidence = extraction.overall_confidence
    elif event.evidence_path and event.kind in ("invoice", "gig_payout", "cash_gig"):
        try:
            extraction, backend = extract(event.evidence_path)
            event.confidence = extraction.overall_confidence
        except Exception:
            # unreadable/corrupt image - fall back so the upload still processes
            extraction, backend = None, "unreadable"
    else:
        backend = "none"

    v = verify_income(
        event.kind, path=event.evidence_path, extraction=extraction,
        reference=event.reference,
        platform_verified=(source and source.kind == "gig_platform"
                           and event.confidence >= 0),
        known=_known_gstins(db))
    event.tier = v["tier"]
    event.verified = v["tier"] == "A"
    event.verification_json = v
    if v.get("irn"):
        event.irn = v["irn"]
    event.fuzzy_key = fuzzy_key(
        source.gstin if source else None, event.gross, event.expected_date)

    db.add(Event(type="income.verified", subject_id=event.id,
                 payload={"tier": v["tier"], "kind": event.kind,
                          "backend": backend, "signals": v["signals"]}))
    if commit:
        db.commit()

    result = {"income_event_id": event.id, "reference": event.reference,
              "kind": event.kind, "verification": v, "backend": backend,
              "confidence": event.confidence}
    result.update(snapshot_and_alerts(db, worker, as_of))
    return result


def request_advance(db: Session, event: IncomeEvent, *, requested: float | None = None,
                    as_of: dt.date | None = None, commit: bool = True) -> dict:
    as_of = as_of or dt.date.today()
    worker = db.get(Worker, event.worker_id)
    snap = resilience.compute(db, worker, as_of)
    v = event.verification_json or {"tier": event.tier}

    d = credit.decide(db, worker=worker, event=event, snapshot=snap,
                      verification=v, confidence=event.confidence or 0.9,
                      requested=requested)

    db.add(Event(type=f"advance.{d['decision']}", subject_id=event.id,
                 payload={"amount": d.get("advance_amount"),
                          "protective": d.get("protective"),
                          "reasons": d["reasons"]}))

    out = {"decision": d, "resilience_before": snap}
    if d["decision"] == "approved":
        adv = Advance(
            income_event_id=event.id, worker_id=worker.id,
            advance_rate=d["advance_rate"], face_value=d["face_value"],
            advance_amount=d["advance_amount"], flat_fee=d["flat_fee"],
            apr_equiv=d["apr_equiv"], net_disbursed=d["net_disbursed"],
            reasons=d["reasons"], decision="approved")
        db.add(adv); db.flush()
        adv.virtual_account = f"STHIR{adv.id[-8:].upper()}@upi"
        legs = allocation.commit(db, adv, worker, savings_slice=worker.autosave_rate)
        worker.cash_buffer = round((worker.cash_buffer or 0) +
                                   sum(l.amount for l in legs
                                       if l.category == "spendable"), 2)
        event.status = "advanced"
        db.add(Event(type="advance.disbursed", subject_id=adv.id,
                     payload={"net": adv.net_disbursed, "va": adv.virtual_account}))
        out["advance_id"] = adv.id
        out["virtual_account"] = adv.virtual_account
        out["allocation"] = [
            {"priority": l.priority, "payee": l.payee, "category": l.category,
             "label": allocation.LABEL.get(l.category, l.category),
             "amount": l.amount} for l in sorted(legs, key=lambda x: x.priority)]

    out["resilience_after"] = resilience.compute(db, worker, as_of)
    if commit:
        db.commit()
    return out
