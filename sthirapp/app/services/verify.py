"""The verification ladder. This is the product's differentiator.

  Tier A  a signed IRP QR was decoded, its signature checks out, and the
          printed body agrees with the signed payload
  Tier B  no usable QR, but the GSTIN passes its checksum and the buyer is
          known to us
  Tier C  neither - an AI reading of a page, and nothing more

A found-but-invalid QR, or a QR whose amount disagrees with the printed
total, is not a decoding failure. It is a fraud signal and it is reported
as one.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from ..schemas import InvoiceExtraction
from . import einvoice_qr as qr
from .gstin import validate as validate_gstin

AMOUNT_TOLERANCE = 1.0     # rupees


def _parse_amount(value) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def verify(path: str | Path, ex: InvoiceExtraction,
           known_buyer_gstins: set[str] | None = None) -> dict:
    known_buyer_gstins = known_buyer_gstins or set()
    read = qr.read_invoice_qr(path)
    payload = read.get("payload") or {}
    mismatches: list[dict] = []
    signals: list[str] = []

    # ---------------- QR present ----------------
    if read["found"]:
        if not read["signature_valid"]:
            mismatches.append({
                "field": "qr_signature",
                "detail": "QR decoded but the IRP signature does not verify",
                "severity": "critical"})
        else:
            signals.append("IRP signature verified")

            qr_amount = _parse_amount(payload.get("TotInvVal"))
            if ex.total_amount is not None and qr_amount is not None:
                if abs(qr_amount - ex.total_amount) > AMOUNT_TOLERANCE:
                    mismatches.append({
                        "field": "total_amount",
                        "detail": (f"printed total {ex.total_amount:,.0f} does not "
                                   f"match signed total {qr_amount:,.0f}"),
                        "signed_value": qr_amount,
                        "printed_value": ex.total_amount,
                        "severity": "critical"})
                else:
                    signals.append("printed total matches signed total")

            if ex.buyer_gstin and payload.get("BuyerGstin"):
                if ex.buyer_gstin.upper() != payload["BuyerGstin"].upper():
                    mismatches.append({
                        "field": "buyer_gstin",
                        "detail": "buyer GSTIN on the page differs from the signed payload",
                        "severity": "critical"})

            if ex.invoice_number and payload.get("DocNo"):
                if ex.invoice_number.upper() != payload["DocNo"].upper():
                    mismatches.append({
                        "field": "invoice_number",
                        "detail": "invoice number differs from the signed payload",
                        "severity": "warning"})

    # ---------------- GSTIN structural check ----------------
    gstin_source = payload.get("BuyerGstin") or ex.buyer_gstin
    gstin_check = validate_gstin(gstin_source)
    if gstin_check["valid"]:
        signals.append(f"buyer GSTIN checksum valid ({gstin_check['state']})")
    elif gstin_source:
        signals.append(f"buyer GSTIN failed: {gstin_check['reason']}")

    known = bool(gstin_source and gstin_source.upper() in
                 {g.upper() for g in known_buyer_gstins})
    if known:
        signals.append("buyer already on the platform")

    # ---------------- tier assignment ----------------
    critical = [m for m in mismatches if m["severity"] == "critical"]
    if read["found"] and read["signature_valid"] and not critical:
        tier = "A"
    elif gstin_check["valid"]:
        tier = "B"
    else:
        tier = "C"

    # A signed invoice whose body contradicts the signature is worse than an
    # unsigned one - we know something is wrong with it.
    if critical:
        tier = "C"
        signals.append("downgraded to tier C: signed payload contradicted by the page")

    return {
        "tier": tier,
        "qr_found": read["found"],
        "signature_valid": read["signature_valid"],
        "irn": payload.get("Irn"),
        "signed_payload": payload or None,
        "gstin_valid": gstin_check["valid"],
        "gstin_state": gstin_check["state"],
        "buyer_known": known,
        "mismatches": mismatches,
        "signals": signals,
    }


def fuzzy_key(buyer_gstin: str | None, amount: float | None,
              due_date: dt.datetime | dt.date | None) -> str:
    """Secondary dedupe key for invoices with no IRN. Amount is bucketed to
    the nearest 100 so OCR jitter in the last digits still collides."""
    g = (buyer_gstin or "NOGSTIN").upper()
    a = int(round((amount or 0) / 100.0))
    d = due_date.strftime("%Y-%m") if due_date else "nodate"
    return f"{g}|{a}|{d}"
