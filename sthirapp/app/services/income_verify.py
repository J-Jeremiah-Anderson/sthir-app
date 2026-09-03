"""Income verification ladder, generalised across income types.

The original invoice ladder (signed QR -> valid GSTIN -> photo) generalises
cleanly to every kind of gig income:

  Tier A  cryptographically or platform verified
            - invoice:     signed e-invoice QR whose payload matches the page
            - gig_payout:  a payout id that reconciles against the linked
                           platform's records (simulated here by a registry)
            - salary:      a bank-credit reference we can corroborate
  Tier B  identifiers valid but not cryptographically proven
            - a checksum-valid GSTIN, or a plausible platform reference
  Tier C  declared only - an AI reading of a screenshot or a cash gig

The tier drives how much we are willing to smooth against the income, exactly
as it drove the advance rate before.
"""
from __future__ import annotations

from pathlib import Path

from ..schemas import InvoiceExtraction
from . import einvoice_qr as qr
from .gstin import validate as validate_gstin

# Simulates the set of payout ids a linked gig platform would confirm. In
# production this is an API call / Account Aggregator pull.
LINKED_PLATFORM_PAYOUTS: set[str] = set()


def register_platform_payout(reference: str) -> None:
    if reference:
        LINKED_PLATFORM_PAYOUTS.add(reference.upper())


def verify_invoice(path: str | Path, ex: InvoiceExtraction,
                   known: set[str] | None = None) -> dict:
    from .verify import verify as _verify_invoice
    return _verify_invoice(path, ex, known)


def verify_payout(reference: str | None, source_kind: str,
                  platform_verified: bool = False) -> dict:
    ref = (reference or "").upper()
    signals: list[str] = []
    if platform_verified or ref in LINKED_PLATFORM_PAYOUTS:
        signals.append("payout reconciled against linked platform records")
        tier = "A"
    elif ref and any(c.isdigit() for c in ref) and len(ref) >= 6:
        signals.append("plausible platform payout reference, not yet reconciled")
        tier = "B"
    else:
        signals.append("declared payout, no platform confirmation")
        tier = "C"
    return {"tier": tier, "qr_found": False, "signature_valid": tier == "A",
            "irn": None, "signed_payload": None, "gstin_valid": False,
            "gstin_state": None, "buyer_known": False, "mismatches": [],
            "signals": signals}


def verify(kind: str, *, path: str | Path | None = None,
           extraction: InvoiceExtraction | None = None,
           reference: str | None = None, platform_verified: bool = False,
           known: set[str] | None = None) -> dict:
    if kind == "invoice" and path and extraction:
        return verify_invoice(path, extraction, known)
    return verify_payout(reference, kind, platform_verified)
