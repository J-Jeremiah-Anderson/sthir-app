"""GST e-invoice QR codes: build, detect, decode, verify.

A real signed e-invoice QR contains a JWS - three base64url segments joined
by dots - whose payload is a small JSON object issued by the Invoice
Registration Portal:

    {"SellerGstin","BuyerGstin","DocNo","DocTyp","DocDt",
     "TotInvVal","ItemCnt","MainHsnCode","Irn","IrnDt"}

We reproduce that structure exactly and sign it with HMAC-SHA256 under a
mock IRP key. The signature check below is therefore real cryptography
against a stand-in key rather than a real NIC public key - swapping in the
production certificate is a one-function change in `verify_signature`.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
from pathlib import Path

import numpy as np
import qrcode
from PIL import Image

from ..config import MOCK_IRP_SECRET


# ---------------------------------------------------------------- encoding

def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64u_decode(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def make_irn(seller_gstin: str, doc_no: str, doc_date: str) -> str:
    """The IRN is a SHA-256 over seller GSTIN + document number + date -
    the same construction the IRP uses, so it is stable and collision-free
    per invoice. This doubles as our strongest dedupe key."""
    return hashlib.sha256(f"{seller_gstin}{doc_no}{doc_date}".encode()).hexdigest()


def build_payload(*, seller_gstin: str, buyer_gstin: str, doc_no: str,
                  doc_date: dt.date, total_value: float, item_count: int = 1,
                  hsn: str = "6109") -> dict:
    doc_dt = doc_date.strftime("%d/%m/%Y")
    irn = make_irn(seller_gstin, doc_no, doc_dt)
    return {
        "SellerGstin": seller_gstin,
        "BuyerGstin": buyer_gstin,
        "DocNo": doc_no,
        "DocTyp": "INV",
        "DocDt": doc_dt,
        "TotInvVal": f"{total_value:.2f}",
        "ItemCnt": str(item_count),
        "MainHsnCode": hsn,          # 6109 = T-shirts/knitted, the Tiruppur staple
        "Irn": irn,
        "IrnDt": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def sign(payload: dict) -> str:
    """Produce the JWS string that goes inside the QR."""
    header = _b64u(json.dumps({"alg": "HS256", "typ": "JWT"},
                              separators=(",", ":")).encode())
    body = _b64u(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode()
    sig = hmac.new(MOCK_IRP_SECRET.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64u(sig)}"


def render(jws: str, box_size: int = 3) -> Image.Image:
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=box_size, border=2)
    qr.add_data(jws)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


# ---------------------------------------------------------------- decoding

def verify_signature(jws: str) -> bool:
    try:
        header, body, sig = jws.split(".")
    except ValueError:
        return False
    expected = hmac.new(MOCK_IRP_SECRET.encode(),
                        f"{header}.{body}".encode(), hashlib.sha256).digest()
    return hmac.compare_digest(_b64u(expected), sig)


def parse(jws: str) -> dict | None:
    try:
        _, body, _ = jws.split(".")
        return json.loads(_b64u_decode(body))
    except Exception:
        return None


def detect_in_image(path: str | Path) -> str | None:
    """Locate and read a QR from a photographed invoice.

    Tries the plain image first, then upscaled, then binarised - a QR on a
    creased, unevenly lit page frequently fails the first pass and succeeds
    on one of the others.
    """
    import cv2

    img = cv2.imread(str(path))
    if img is None:
        return None
    detector = cv2.QRCodeDetector()

    attempts = [img]
    h, w = img.shape[:2]
    attempts.append(cv2.resize(img, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC))
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    attempts.append(cv2.cvtColor(
        cv2.adaptiveThreshold(grey, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                              cv2.THRESH_BINARY, 31, 5),
        cv2.COLOR_GRAY2BGR))

    for candidate in attempts:
        try:
            data, points, _ = detector.detectAndDecode(candidate)
        except cv2.error:
            continue
        if data:
            return data
    return None


def read_invoice_qr(path: str | Path) -> dict:
    """The pipeline entry point.

    Returns {found, signature_valid, payload, raw}. A found-but-unsigned QR
    is a fraud signal, not a decoding failure - keep the two distinct.
    """
    raw = detect_in_image(path)
    if not raw:
        return {"found": False, "signature_valid": False, "payload": None, "raw": None}
    payload = parse(raw)
    if payload is None:
        return {"found": True, "signature_valid": False, "payload": None, "raw": raw}
    return {
        "found": True,
        "signature_valid": verify_signature(raw),
        "payload": payload,
        "raw": raw,
    }
