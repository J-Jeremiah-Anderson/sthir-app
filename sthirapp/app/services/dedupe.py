"""The double-financing guard.

Three independent keys, checked in order of strength:

  1. IRN         - a SHA-256 the IRP derives from seller + doc no + date.
                   An exact match is conclusive.
  2. fuzzy key   - buyer GSTIN + amount bucket + due month. Catches the same
                   invoice re-typed or re-read with slightly different OCR.
  3. image phash - catches a re-photograph of a page already submitted, even
                   when every field was retyped.

This is the first question any credit person asks, and the cheapest one to
answer well.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Invoice
from . import phash

FUNDED = ("funded", "settled")


def check(db: Session, *, irn: str | None, fuzzy: str | None,
          image_hash: str | None, exclude_id: str | None = None) -> dict:
    """Returns {duplicate, matched_invoice_id, method, detail, distance}."""

    def _rows():
        stmt = select(Invoice).where(Invoice.status.in_(FUNDED))
        if exclude_id:
            stmt = stmt.where(Invoice.id != exclude_id)
        return db.execute(stmt).scalars().all()

    rows = _rows()

    if irn:
        for r in rows:
            if r.irn and r.irn == irn:
                return {"duplicate": True, "matched_invoice_id": r.id,
                        "matched_number": r.number, "method": "irn",
                        "detail": f"IRN already funded on invoice {r.number}",
                        "distance": 0}

    if fuzzy:
        for r in rows:
            if r.fuzzy_key and r.fuzzy_key == fuzzy:
                return {"duplicate": True, "matched_invoice_id": r.id,
                        "matched_number": r.number, "method": "fuzzy_key",
                        "detail": (f"same buyer, amount and due month as funded "
                                   f"invoice {r.number}"),
                        "distance": 0}

    if image_hash:
        best = None
        for r in rows:
            if not r.image_phash:
                continue
            dup, dist = phash.is_duplicate(image_hash, r.image_phash)
            if dup and (best is None or dist < best[1]):
                best = (r, dist)
        if best:
            r, dist = best
            return {"duplicate": True, "matched_invoice_id": r.id,
                    "matched_number": r.number, "method": "image_phash",
                    "detail": (f"image matches funded invoice {r.number} "
                               f"(hamming {dist}/255) - re-photograph of a page "
                               f"already financed"),
                    "distance": dist}

    return {"duplicate": False, "matched_invoice_id": None,
            "matched_number": None, "method": None, "detail": None,
            "distance": None}
