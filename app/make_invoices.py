"""Generates the synthetic invoice dataset.

Three document classes, matching the three verification tiers:

  A  GST e-invoice, printed, carrying a signed IRP QR code
  B  printed tax invoice, valid GSTIN, no QR (pre-mandate or below threshold)
  C  handwritten bilingual delivery slip - no GSTIN, no QR

Plus two adversarial cases the demo needs: a re-photographed duplicate of an
already-funded invoice, and one where the printed total disagrees with the
signed QR payload.

Every image is then put through a `photograph` pass - rotation, uneven
lighting, sensor noise, JPEG recompression - because a QR decoder that only
works on pristine renders proves nothing.
"""
from __future__ import annotations

import datetime as dt
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from .config import INVOICE_DIR
from .services import einvoice_qr as qr

FONTS = Path("/System/Library/Fonts/Supplemental")
CORE = Path("/System/Library/Fonts")


def _font(name: str, size: int):
    for base in (FONTS, CORE):
        p = base / name
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                pass
    return ImageFont.load_default()


def F(size, bold=False):        # printed body
    return _font("Arial Bold.ttf" if bold else "Arial.ttf", size)


def MONO(size):
    return _font("Courier New.ttf", size)


def UNI(size):                  # Tamil-capable
    return _font("Arial Unicode.ttf", size)


def HAND(size):
    return _font("Bradley Hand Bold.ttf", size)


W, H = 1000, 1400
INK = (24, 26, 32)
FADE = (95, 100, 112)


# ------------------------------------------------------------ printed forms

def _header(d: ImageDraw.ImageDraw, mill: dict, title: str, sub: str = ""):
    d.rectangle([0, 0, W, 118], fill=(238, 240, 246))
    d.text((48, 30), mill["name"].upper(), font=F(30, True), fill=INK)
    d.text((48, 70), f"{mill['address']}  |  GSTIN {mill['gstin']}",
           font=F(15), fill=FADE)
    d.text((W - 48, 34), title, font=F(20, True), fill=(43, 58, 140), anchor="ra")
    if sub:
        d.text((W - 48, 66), sub, font=MONO(13), fill=FADE, anchor="ra")
    d.line([40, 118, W - 40, 118], fill=(190, 195, 208), width=2)


def _line_items(d: ImageDraw.ImageDraw, y: int, items: list[dict], total: float):
    d.rectangle([48, y, W - 48, y + 34], fill=(246, 247, 250))
    for x, label in ((60, "DESCRIPTION"), (560, "HSN"), (660, "QTY"),
                     (770, "RATE"), (900, "AMOUNT")):
        d.text((x, y + 11), label, font=F(12, True), fill=FADE)
    y += 44
    for it in items:
        d.text((60, y), it["desc"], font=F(15), fill=INK)
        d.text((560, y), it["hsn"], font=MONO(14), fill=INK)
        d.text((660, y), f"{it['qty']:,}", font=MONO(14), fill=INK)
        d.text((770, y), f"{it['rate']:,.2f}", font=MONO(14), fill=INK)
        d.text((952, y), f"{it['amount']:,.2f}", font=MONO(14), fill=INK, anchor="ra")
        y += 30
    d.line([48, y + 8, W - 48, y + 8], fill=(210, 214, 224))
    y += 22
    d.text((770, y), "TOTAL", font=F(16, True), fill=INK)
    d.text((952, y), f"{total:,.2f}", font=F(16, True), fill=INK, anchor="ra")
    return y + 40


def render_tier_a(spec: dict) -> Image.Image:
    """Full GST e-invoice with a signed QR."""
    img = Image.new("RGB", (W, H), (253, 253, 251))
    d = ImageDraw.Draw(img)
    payload = qr.build_payload(
        seller_gstin=spec["mill"]["gstin"], buyer_gstin=spec["buyer"]["gstin"],
        doc_no=spec["number"], doc_date=spec["issue_date"],
        total_value=spec["qr_amount"], item_count=len(spec["items"]))
    jws = qr.sign(payload)

    _header(d, spec["mill"], "TAX INVOICE", "e-Invoice / IRP authenticated")

    y = 145
    d.text((48, y), "BILL TO", font=F(12, True), fill=FADE)
    d.text((48, y + 22), spec["buyer"]["name"], font=F(19, True), fill=INK)
    d.text((48, y + 50), spec["buyer"]["address"], font=F(14), fill=FADE)
    d.text((48, y + 72), f"GSTIN  {spec['buyer']['gstin']}", font=MONO(14), fill=INK)

    for i, (k, v) in enumerate([
        ("Invoice No", spec["number"]),
        ("Invoice Date", spec["issue_date"].strftime("%d/%m/%Y")),
        ("Due Date", spec["due_date"].strftime("%d/%m/%Y")),
        ("Terms", f"Net {spec['terms']} days"),
    ]):
        d.text((600, y + i * 26), k, font=F(13), fill=FADE)
        d.text((790, y + i * 26), v, font=MONO(14), fill=INK)

    y = _line_items(d, y + 115, spec["items"], spec["printed_amount"])

    # IRN block + QR, exactly where the IRP mandates it
    qimg = qr.render(jws, box_size=4)
    img.paste(qimg, (W - 48 - qimg.width, y + 10))
    d.text((48, y + 16), "IRN", font=F(12, True), fill=FADE)
    irn = payload["Irn"]
    d.text((48, y + 36), irn[:32], font=MONO(13), fill=INK)
    d.text((48, y + 56), irn[32:], font=MONO(13), fill=INK)
    d.text((48, y + 86), "Ack Date", font=F(12, True), fill=FADE)
    d.text((48, y + 106), payload["IrnDt"], font=MONO(13), fill=INK)
    d.text((48, y + 140), "Digitally signed by NIC / Invoice Registration Portal",
           font=F(12), fill=FADE)

    y += 210
    d.text((48, y), "Amount in words", font=F(12, True), fill=FADE)
    d.text((48, y + 20), spec["words"], font=F(15), fill=INK)
    d.text((W - 48, y + 70), f"For {spec['mill']['name']}", font=F(14),
           fill=INK, anchor="ra")
    d.text((W - 48, y + 118), "Authorised Signatory", font=F(12),
           fill=FADE, anchor="ra")
    d.text((W - 120, y + 96), spec["mill"]["name"].split()[0], font=HAND(26),
           fill=(30, 45, 120), anchor="ra")
    return img


def render_tier_b(spec: dict) -> Image.Image:
    """Plain printed tax invoice - valid GSTIN, no IRP QR."""
    img = Image.new("RGB", (W, H), (252, 251, 247))
    d = ImageDraw.Draw(img)
    _header(d, spec["mill"], "INVOICE", "")

    y = 150
    d.text((48, y), "TO", font=F(12, True), fill=FADE)
    d.text((48, y + 20), spec["buyer"]["name"], font=F(19, True), fill=INK)
    d.text((48, y + 48), f"GSTIN {spec['buyer']['gstin']}", font=MONO(14), fill=INK)
    d.text((640, y), f"No. {spec['number']}", font=F(15), fill=INK)
    d.text((640, y + 24), spec["issue_date"].strftime("Date: %d/%m/%Y"),
           font=F(15), fill=INK)
    d.text((640, y + 48), f"Terms: {spec['terms']} days", font=F(15), fill=FADE)

    y = _line_items(d, y + 100, spec["items"], spec["printed_amount"])
    d.text((48, y + 10), spec["words"], font=F(15), fill=INK)
    d.text((48, y + 60), "Goods once sold will not be taken back.",
           font=F(12), fill=FADE)
    d.text((48, y + 82), "Subject to Tiruppur jurisdiction.", font=F(12), fill=FADE)
    d.text((W - 100, y + 70), spec["mill"]["name"].split()[0], font=HAND(26),
           fill=(30, 45, 120), anchor="ra")
    d.text((W - 48, y + 110), "Authorised Signatory", font=F(12),
           fill=FADE, anchor="ra")
    return img


def render_tier_c(spec: dict) -> Image.Image:
    """Handwritten bilingual delivery slip on ruled paper."""
    img = Image.new("RGB", (W, 1000), (250, 246, 232))
    d = ImageDraw.Draw(img)
    for i in range(6, 1000, 46):                       # ruled lines
        d.line([40, i, W - 40, i], fill=(205, 212, 226), width=1)
    d.line([90, 0, 90, 1000], fill=(226, 178, 178), width=2)   # margin rule

    d.text((110, 30), spec["mill"]["name"], font=HAND(30), fill=(28, 40, 96))
    d.text((110, 74), "Delivery Note / டெலிவரி நோட்டு", font=UNI(20),
           fill=(60, 66, 82))
    d.text((700, 34), spec["issue_date"].strftime("%d-%m-%Y"), font=HAND(26),
           fill=(28, 40, 96))
    d.text((700, 74), f"No. {spec['number']}", font=HAND(24), fill=(28, 40, 96))

    lines = spec["hand_lines"]
    y = 150
    for ln in lines:
        font = UNI(24) if any(ord(c) > 0x0B00 for c in ln) else HAND(28)
        d.text((110, y), ln, font=font, fill=(30, 34, 60))
        y += 46

    d.text((110, y + 30), "Total", font=HAND(30), fill=(30, 34, 60))
    d.text((420, y + 30), f"Rs. {spec['printed_amount']:,.0f}/-",
           font=HAND(32), fill=(140, 30, 30))
    d.text((110, y + 96), "Received the goods in good condition",
           font=HAND(22), fill=(70, 76, 92))
    d.text((620, y + 130), "கையொப்பம்", font=UNI(20), fill=(70, 76, 92))
    d.text((620, y + 96), spec["buyer"]["name"].split()[0], font=HAND(28),
           fill=(28, 40, 96))
    return img


# ------------------------------------------------------------ camera pass

def photograph(img: Image.Image, seed: int, harsh: bool = False) -> Image.Image:
    """Make it look like a phone photo of a piece of paper."""
    rng = random.Random(seed)
    angle = rng.uniform(-2.6, 2.6) * (2.0 if harsh else 1.0)
    img = img.rotate(angle, resample=Image.Resampling.BICUBIC,
                     expand=True, fillcolor=(232, 230, 226))

    arr = np.asarray(img).astype(np.float32)
    h, w = arr.shape[:2]

    # uneven lighting: a soft diagonal gradient plus a bright corner
    yy, xx = np.mgrid[0:h, 0:w]
    grad = 1.0 - 0.20 * (xx / w) - 0.13 * (yy / h)
    cx, cy = rng.uniform(0.1, 0.5) * w, rng.uniform(0.05, 0.4) * h
    glare = 0.16 * np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * (0.34 * w) ** 2)))
    arr *= (grad + glare)[..., None]

    noise_sd = 7.0 if harsh else 3.2
    arr += np.random.default_rng(seed).normal(0, noise_sd, arr.shape)
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

    if harsh:
        out = out.filter(ImageFilter.GaussianBlur(0.7))
    return out


def save_jpeg(img: Image.Image, path: Path, quality: int = 72):
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(path, "JPEG", quality=quality, optimize=True)
    return path


def rephotograph(path: Path, out: Path, seed: int) -> Path:
    """Simulate someone re-shooting an invoice they already submitted -
    the classic double-financing attempt."""
    img = Image.open(path)
    img = photograph(img, seed=seed, harsh=True)
    return save_jpeg(img, out, quality=58)
