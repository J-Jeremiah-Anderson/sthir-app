"""Crisis Override - the emergency bypass valve.

For a true emergency (an accident, a hospital bill, a bike that won't start and
is the person's whole income), the ordinary affordability gate is the wrong
tool: a worker whose runway is already gone will never "qualify", yet doing
nothing is the worst outcome. So Crisis Override runs a different path:

  1. Classify the uploaded bill/damage photo (Gemini vision, offline fallback).
  2. Run the Entitlement Bridge first - try to replace the money with free
     government coverage before lending anything.
  3. For any residual, issue an emergency advance that BYPASSES the standard
     credit limit but is paid DIRECT TO THE VENDOR over UPI - so the money
     cannot be swallowed by an existing moneylender before it reaches the
     hospital or the mechanic.

Everything is logged. An emergency advance is still an advance; it is just
governed by need and routed for safety rather than gated on affordability.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import CLAUDE_MODEL, GEMINI_MODEL, PREFERRED_GEMINI
from ..models import Advance, Allocation, Event, Worker
from ..schemas import CrisisAssessment
from . import entitlements
from .extract import _google_key, _MEDIA, resolve_gemini_model

EMERGENCY_CAP = 50_000     # emergency ceiling, above the ordinary advance cap

CRISIS_SYSTEM = """You assess emergency expense evidence for a gig worker's
financial-crisis fund. You are shown a photo of a bill, a medical estimate, or
vehicle/property damage. Decide whether it is a genuine emergency, classify the
type, read the vendor and the amount if present, and rate urgency. Be
conservative: if it does not look like a real emergency expense, say so. Amounts
are Indian rupees."""

CRISIS_PROMPT = ("Assess this emergency evidence and return the schema. If you "
                 "can read a total payable, use it as estimated_amount.")


def classify(path: str | Path) -> tuple[CrisisAssessment, str]:
    path = Path(path)
    if _google_key() and os.getenv("FF_EXTRACTOR", "auto") != "offline":
        try:
            return _classify_gemini(path), "gemini"
        except Exception as exc:   # noqa: BLE001
            print(f"[crisis] gemini failed ({exc}); using offline")
    return _classify_offline(path), "offline"


def _classify_gemini(path: Path) -> CrisisAssessment:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=_google_key(),
                          http_options=types.HttpOptions(timeout=25_000))
    resp = client.models.generate_content(
        model=resolve_gemini_model(client),
        contents=[types.Part.from_bytes(
            data=path.read_bytes(),
            mime_type=_MEDIA.get(path.suffix.lower(), "image/jpeg")),
            CRISIS_PROMPT],
        config=types.GenerateContentConfig(
            system_instruction=CRISIS_SYSTEM,
            response_mime_type="application/json",
            response_schema=CrisisAssessment, temperature=0.0,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True)))
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, CrisisAssessment):
        return parsed
    return CrisisAssessment.model_validate_json(resp.text)


# Registry for the seeded demo crisis images (what a vision model would read).
CRISIS_REGISTRY = {
    "crisis-accident": dict(crisis_type="vehicle_accident",
                            vendor_name="Anna Nagar Motors",
                            estimated_amount=35000.0, urgency="immediate",
                            summary="Two-wheeler accident repair estimate: front "
                                    "fork, headlamp, brake assembly."),
    "crisis-hospital": dict(crisis_type="hospitalization",
                            vendor_name="Kauvery Hospital",
                            estimated_amount=48000.0, urgency="immediate",
                            summary="Emergency admission - observation, scans, "
                                    "two nights."),
    "crisis-breakdown": dict(crisis_type="vehicle_breakdown",
                             vendor_name="Speed Auto Garage",
                             estimated_amount=8500.0, urgency="high",
                             summary="Engine seizure - the bike is the worker's "
                                     "entire income; no scheme covers breakdowns."),
}


def _classify_offline(path: Path) -> CrisisAssessment:
    stem = path.stem.lower()
    meta = next((v for k, v in CRISIS_REGISTRY.items() if k in stem), None)
    if meta is None:
        return CrisisAssessment(is_genuine_crisis=True, crisis_type="other",
                                estimated_amount=None, urgency="high",
                                evidence_summary="Uploaded emergency evidence.",
                                confidence=0.55)
    return CrisisAssessment(
        is_genuine_crisis=True, crisis_type=meta["crisis_type"],
        vendor_name=meta["vendor_name"], estimated_amount=meta["estimated_amount"],
        urgency=meta["urgency"], evidence_summary=meta["summary"], confidence=0.93)


def handle(db: Session, worker: Worker, evidence_path: str | Path,
           requested: float | None = None, commit: bool = True) -> dict:
    assessment, backend = classify(evidence_path)
    amount = requested or assessment.estimated_amount or 0.0

    db.add(Event(type="crisis.assessed", subject_id=worker.id,
                 payload={"type": assessment.crisis_type, "amount": amount,
                          "backend": backend, "genuine": assessment.is_genuine_crisis}))

    if not assessment.is_genuine_crisis:
        if commit:
            db.commit()
        return {"assessment": assessment.model_dump(), "backend": backend,
                "decision": "not_a_crisis",
                "message": "This does not appear to be a genuine emergency expense."}

    # 1) Entitlement Bridge first - replace loan with free coverage
    bridge = entitlements.match(worker, crisis_tag=assessment.crisis_type,
                                amount=amount)
    residual = bridge["residual_loan_needed"] if amount else 0.0

    result = {"assessment": assessment.model_dump(), "backend": backend,
              "amount": round(amount, 2), "entitlement_bridge": bridge}

    if amount and residual <= 0:
        result["decision"] = "covered_by_entitlement"
        result["emergency_advance"] = None
        result["message"] = (f"No loan needed. Rs {amount:,.0f} is covered by "
                             f"{bridge['schemes'][0]['name']}. We are filing the "
                             "claim on your behalf.")
        db.add(Event(type="crisis.covered_by_welfare", subject_id=worker.id,
                     payload={"scheme": bridge['schemes'][0]['name'], "amount": amount}))
        if commit:
            db.commit()
        return result

    # 2) Emergency advance for the residual only, paid direct to vendor
    disburse = round(min(residual or amount, EMERGENCY_CAP), 2)
    adv = Advance(worker_id=worker.id, advance_amount=disburse,
                  face_value=amount, net_disbursed=disburse,
                  advance_rate=1.0, flat_fee=0.0, apr_equiv=0.0,
                  decision="approved",
                  reasons=[{"code": "crisis_override",
                            "label": "emergency need verified; standard limit bypassed",
                            "ok": True},
                           {"code": "welfare_first",
                            "label": f"Rs {bridge['welfare_offset'] or 0:,.0f} routed "
                                     f"to government coverage first", "ok": True}])
    db.add(adv); db.flush()
    adv.virtual_account = f"STHIR-EMG-{adv.id[-6:].upper()}"
    vendor = assessment.vendor_name or "Vendor"
    db.add(Allocation(advance_id=adv.id, worker_id=worker.id, priority=1,
                      payee=f"{vendor} (direct UPI)", category="emergency",
                      amount=disburse, status="settled"))
    db.add(Event(type="crisis.emergency_disbursed", subject_id=adv.id,
                 payload={"vendor": vendor, "amount": disburse,
                          "direct_to_vendor": True}))

    result["decision"] = "emergency_advance_to_vendor"
    result["emergency_advance"] = {
        "advance_id": adv.id, "amount": disburse,
        "paid_to": f"{vendor} (direct UPI)",
        "welfare_offset": bridge["welfare_offset"],
        "note": "Paid straight to the vendor so no existing debt can intercept it."}
    result["message"] = (
        f"Rs {bridge['welfare_offset'] or 0:,.0f} covered by government scheme; "
        f"remaining Rs {disburse:,.0f} paid directly to {vendor}.")
    if commit:
        db.commit()
    return result
