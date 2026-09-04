"""Extraction: photograph -> strict JSON.

Two backends behind one function. `claude` sends the image to a vision model
and constrains the reply with a Pydantic schema. `offline` is a deterministic
extractor that reads the signed QR (real work - no model needed) and fills
the remaining fields from the document registry, so the entire pipeline runs
with no API key and no network. The demo therefore cannot fail on a missing
credential, which is the point.
"""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

from ..config import (CLAUDE_MODEL, EXTRACTOR, GEMINI_MODEL,
                       PREFERRED_GEMINI)
from ..schemas import FieldConfidence, InvoiceExtraction, LineItem
from . import einvoice_qr as qr
from .gstin import make_valid

SYSTEM = """You read invoices from Indian textile MSMEs in Tiruppur, Tamil Nadu.

Documents arrive as phone photographs and may be:
  - a printed GST tax invoice or e-invoice
  - a handwritten delivery note, often bilingual

Bilingual notes mix Tamil script, romanised Tamil ("Tanglish") and English.
Read all three. Common trade vocabulary you must understand:
  banian / பனியன் = knitted undershirt      thuni / துணி = cloth
  nool / நூல் = yarn                        kooli = wages
  kaasu = money/payment                     lorry la anuppiyachu = dispatched by lorry
  varum = will come/arrive                  moonu = three, rendu = two
  "3 lakh" = 300000                         "-ku" = "to/for" (dative suffix)

Rules:
  - Report amounts as plain numbers: "Rs. 3,00,000/-" -> 300000
  - Indian digit grouping is lakh/crore: 3,00,000 is three hundred thousand
  - Never invent a GSTIN. If you cannot read one, return null
  - Confidence is per field, 0.0-1.0, and must be honest. A blurred or
    ambiguous digit in the total is a low confidence, not a guess
  - Put any vernacular line you used into vernacular_notes with its meaning
"""

PROMPT = (
    "You are validating a proof of income for a gig / informal worker. "
    "FIRST decide whether this image is a genuine income or financial document "
    "- an invoice, a gig-platform payout screenshot, a receipt, a bill, or a "
    "payslip. If it is NOT (for example a selfie or photo of a person, a "
    "landscape, a screenshot of a chat or an unrelated app, a meme, or anything "
    "with no monetary / income content), set is_income_document=false, "
    "document_type='not_income', overall_confidence below 0.15, and leave the "
    "money fields null. OTHERWISE set is_income_document=true, pick the closest "
    "document_type, and extract every field you can read into the schema.")

_MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
          ".png": "image/png", ".webp": "image/webp"}


def _google_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def _backend() -> str:
    if EXTRACTOR in ("gemini", "claude", "offline"):
        return EXTRACTOR
    if _google_key():
        return "gemini"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "claude"
    return "offline"


# ------------------------------------------------------------------ gemini

_resolved_model: str | None = None


def resolve_gemini_model(client) -> str:
    """Ask the API which models it serves and pick the best one we know.

    Model IDs are renamed and retired often enough that pinning one in source
    is a real demo risk. An explicit FF_GEMINI_MODEL always wins.
    """
    global _resolved_model
    if GEMINI_MODEL:
        return GEMINI_MODEL
    if _resolved_model:
        return _resolved_model

    try:
        available = []
        for m in client.models.list():
            actions = getattr(m, "supported_actions", None) or []
            if actions and "generateContent" not in actions:
                continue
            available.append((m.name or "").removeprefix("models/"))
        for want in PREFERRED_GEMINI:
            for name in available:
                if name == want or name.startswith(want + "-"):
                    _resolved_model = name
                    print(f"[extract] using Gemini model {name}")
                    return name
        vision = [n for n in available if "flash" in n or "pro" in n]
        if vision:
            _resolved_model = sorted(vision)[-1]
            print(f"[extract] falling back to Gemini model {_resolved_model}")
            return _resolved_model
    except Exception as exc:   # noqa: BLE001
        print(f"[extract] could not list Gemini models ({exc})")

    _resolved_model = PREFERRED_GEMINI[-1]
    return _resolved_model


def extract_with_gemini(path: str | Path) -> InvoiceExtraction:
    from google import genai
    from google.genai import types

    path = Path(path)
    # Hard 25s HTTP timeout so a slow/overloaded model can never hang an upload.
    client = genai.Client(api_key=_google_key(),
                          http_options=types.HttpOptions(timeout=25_000))
    model = resolve_gemini_model(client)

    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(
                data=path.read_bytes(),
                mime_type=_MEDIA.get(path.suffix.lower(), "image/jpeg")),
            PROMPT,
        ],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM,
            response_mime_type="application/json",
            response_schema=InvoiceExtraction,
            temperature=0.0,
            # structured JSON only - stop the SDK's automatic function-calling
            # loop that was stalling newer models on this call.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True),
        ),
    )
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, InvoiceExtraction):
        return parsed
    return InvoiceExtraction.model_validate_json(response.text)


# ------------------------------------------------------------------ claude

def extract_with_claude(path: str | Path) -> InvoiceExtraction:
    import anthropic

    path = Path(path)
    data = base64.standard_b64encode(path.read_bytes()).decode()
    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=CLAUDE_MODEL,
        max_tokens=8000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": _MEDIA.get(path.suffix.lower(), "image/jpeg"),
                    "data": data}},
                {"type": "text", "text": PROMPT},
            ],
        }],
        output_format=InvoiceExtraction,
    )
    return response.parsed_output


# ----------------------------------------------------------------- offline

# Ground truth for the seeded documents. The offline backend still does real
# work (QR decode, amount parsing); this only supplies what a vision model
# would otherwise read off the page.
REGISTRY: dict[str, dict] = {
    "INV-4471": dict(buyer="Sree Andal Exports", terms=90, handwritten=False),
    "INV-4473": dict(buyer="Sree Andal Exports", terms=75, handwritten=False),
    "INV-8802": dict(buyer="Lakshmi Apparels", terms=60, handwritten=False),
    "INV-2210": dict(buyer="Bharat Retail Ltd", terms=45, handwritten=False),
    "INV-8804": dict(buyer="Lakshmi Apparels", terms=60, handwritten=False,
                     printed_total=540000.0),
    "INV-4472": dict(buyer="Kovai Knits Pvt Ltd", terms=92, handwritten=False,
                     gstin=make_valid("33AADCK3344M1Z"), printed_total=185000.0,
                     seller="Ponnusamy Textiles", date="20/08/2026"),
    "INV-8803": dict(buyer="Kovai Knits Pvt Ltd", terms=60, handwritten=False,
                     gstin=make_valid("33AADCK3344M1Z"), printed_total=210000.0,
                     seller="Vetri Knitwear", date="26/08/2026"),
    "DN-115": dict(buyer="Meenakshi Garments", terms=30, handwritten=True,
                   printed_total=96000.0, seller="Sakthi Knit Fabrics",
                   date="30/08/2026"),
}


def _stem_to_docno(path: Path) -> str | None:
    stem = path.stem.upper().replace("-RESUBMIT", "")
    m = re.match(r"^(INV|DN)-?(\d+)$", stem)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def extract_offline(path: str | Path) -> InvoiceExtraction:
    path = Path(path)
    read = qr.read_invoice_qr(path)
    payload = read.get("payload") or {}
    doc_no = payload.get("DocNo") or _stem_to_docno(path)
    meta = REGISTRY.get(doc_no or "", {})

    handwritten = meta.get("handwritten", False)
    # A handwritten slip genuinely reads worse. Reflect that honestly.
    base_conf = 0.62 if handwritten else (0.97 if read["found"] else 0.90)

    total = meta.get("printed_total")
    if total is None and payload.get("TotInvVal"):
        total = float(payload["TotInvVal"])

    conf = FieldConfidence(
        buyer_name=base_conf,
        buyer_gstin=0.0 if handwritten else base_conf,
        invoice_number=base_conf,
        total_amount=0.58 if handwritten else base_conf,
        dates=0.55 if handwritten else base_conf,
    )

    notes = []
    if handwritten:
        notes = [
            "வெள்ளை பனியன் 600 pcs @ 96 -> white banian, 600 pieces at Rs.96",
            "Lorry la anuppiyachu 30-08 -> dispatched by lorry on 30 Aug",
            "Kaasu next month 1st varum -> payment expected 1st of next month",
        ]

    return InvoiceExtraction(
        document_type=("delivery_note" if handwritten else
                       ("e_invoice" if read["found"] else "tax_invoice")),
        is_handwritten=handwritten,
        languages=(["ta", "en", "ta-Latn"] if handwritten else ["en"]),
        seller_name=meta.get("seller") or payload.get("SellerGstin") and meta.get("seller"),
        seller_gstin=payload.get("SellerGstin"),
        buyer_name=meta.get("buyer"),
        buyer_gstin=payload.get("BuyerGstin") or meta.get("gstin"),
        invoice_number=doc_no,
        invoice_date=payload.get("DocDt") or meta.get("date"),
        payment_terms_days=meta.get("terms"),
        total_amount=total,
        line_items=[LineItem(description="Knitted cotton garments",
                             hsn_code=payload.get("MainHsnCode"),
                             amount=total)] if total else [],
        irn_printed=payload.get("Irn"),
        vernacular_notes=notes,
        field_confidence=conf,
        overall_confidence=round(
            min(conf.buyer_name, conf.invoice_number, conf.total_amount), 3),
    )


def extract(path: str | Path) -> tuple[InvoiceExtraction, str]:
    """Returns (extraction, backend_used). Falls back rather than failing."""
    backend = _backend()
    if backend == "gemini":
        try:
            return extract_with_gemini(path), "gemini"
        except Exception as exc:      # noqa: BLE001 - demo must not die here
            print(f"[extract] gemini backend failed ({exc}); using offline")
    elif backend == "claude":
        try:
            return extract_with_claude(path), "claude"
        except Exception as exc:      # noqa: BLE001
            print(f"[extract] claude backend failed ({exc}); using offline")
    return extract_offline(path), "offline"
