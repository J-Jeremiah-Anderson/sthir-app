"""Generates gig-payout evidence: phone-style payout screenshots.

A delivery-app weekly earnings screen, rendered as a phone screenshot, so the
extraction pipeline has something real to read for gig income - the analogue
of the printed invoice for the tailor persona.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import DATA_DIR
from .make_invoices import F, MONO, save_jpeg, photograph

EVID = DATA_DIR / "evidence"
W, H = 720, 1280


def payout_screenshot(*, platform: str, rider: str, payout_id: str,
                      amount: float, orders: int, week: str,
                      brand=(255, 90, 40)) -> Image.Image:
    img = Image.new("RGB", (W, H), (247, 248, 250))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 190], fill=brand)
    d.text((40, 50), platform, font=F(34, True), fill=(255, 255, 255))
    d.text((40, 100), "Weekly earnings", font=F(20), fill=(255, 255, 255))
    d.text((40, 132), week, font=MONO(16), fill=(255, 240, 235))

    d.text((40, 250), "Total payout", font=F(20), fill=(110, 116, 128))
    d.text((40, 290), f"Rs {amount:,.0f}", font=F(64, True), fill=(24, 26, 32))

    d.line([40, 400, W - 40, 400], fill=(224, 227, 232), width=1)
    rows = [("Orders delivered", str(orders)),
            ("Payout ID", payout_id),
            ("Status", "Paid to bank a/c ****4471"),
            ("Incentives", f"Rs {amount * 0.12:,.0f}"),
            ("Fuel adjustment", f"- Rs {amount * 0.08:,.0f}")]
    y = 430
    for k, v in rows:
        d.text((40, y), k, font=F(20), fill=(110, 116, 128))
        d.text((W - 40, y), v, font=MONO(19), fill=(24, 26, 32), anchor="ra")
        y += 56

    d.rectangle([40, y + 30, W - 40, y + 110], outline=(224, 227, 232), width=1)
    d.text((60, y + 55), rider, font=F(22, True), fill=(24, 26, 32))
    d.text((60, y + 84), "Partner since 2023", font=F(15), fill=(150, 156, 168))
    d.text((40, H - 60), "This is your official earnings summary.",
           font=F(15), fill=(150, 156, 168))
    return img


def build(seed_registry) -> list[dict]:
    """Render one payout screenshot per spec, photographed lightly."""
    out = []
    for i, spec in enumerate(seed_registry):
        img = payout_screenshot(**{k: v for k, v in spec.items()
                                   if k in ("platform", "rider", "payout_id",
                                            "amount", "orders", "week", "brand")})
        img = photograph(img, seed=100 + i, harsh=False)
        path = EVID / f"{spec['payout_id'].lower()}.jpg"
        save_jpeg(img, path, quality=80)
        out.append({"path": path, **spec})
    return out


def crisis_bill(*, title: str, vendor: str, lines: list[tuple[str, float]],
                total: float, stamp: str, brand=(178, 58, 56)) -> Image.Image:
    """A repair estimate / hospital bill for the Crisis Override demo."""
    img = Image.new("RGB", (760, 1040), (253, 252, 250))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 760, 120], fill=brand)
    d.text((40, 34), vendor, font=F(28, True), fill=(255, 255, 255))
    d.text((40, 76), title, font=F(17), fill=(255, 235, 233))
    d.text((720, 40), stamp, font=MONO(14), fill=(255, 235, 233), anchor="ra")

    y = 170
    d.text((40, y), "ESTIMATE / BILL", font=F(13, True), fill=(120, 126, 136))
    d.line([40, y + 26, 720, y + 26], fill=(220, 224, 230), width=1)
    y += 50
    for label, amt in lines:
        d.text((40, y), label, font=F(17), fill=(30, 34, 42))
        d.text((720, y), f"Rs {amt:,.0f}", font=MONO(16), fill=(30, 34, 42), anchor="ra")
        y += 40
    d.line([40, y + 6, 720, y + 6], fill=(200, 205, 214), width=1)
    y += 26
    d.text((40, y), "TOTAL PAYABLE", font=F(19, True), fill=(30, 34, 42))
    d.text((720, y), f"Rs {total:,.0f}", font=F(22, True), fill=brand, anchor="ra")
    d.text((40, y + 70), "Payment due on collection.", font=F(14), fill=(120, 126, 136))
    d.text((40, y + 96), f"{vendor}  |  UPI: {vendor.split()[0].lower()}@okhdfc",
           font=MONO(13), fill=(120, 126, 136))
    return img


def build_crises() -> list[dict]:
    specs = [
        dict(key="crisis-accident", title="Two-wheeler accident repair estimate",
             vendor="Anna Nagar Motors", stamp="EST-4471",
             lines=[("Front fork replacement", 12500), ("Headlamp assembly", 6800),
                    ("Brake assembly + labour", 9200), ("Body work & paint", 6500)],
             total=35000, brand=(178, 58, 56)),
        dict(key="crisis-hospital", title="Emergency admission - provisional bill",
             vendor="Kauvery Hospital", stamp="IP-90231",
             lines=[("Casualty & observation", 14000), ("CT scan + X-ray", 11000),
                    ("Room (2 nights)", 12000), ("Medicines & consumables", 11000)],
             total=48000, brand=(38, 96, 120)),
        dict(key="crisis-breakdown", title="Two-wheeler breakdown - repair estimate",
             vendor="Speed Auto Garage", stamp="RE-7781",
             lines=[("Engine cylinder rework", 4200), ("Piston + rings", 2100),
                    ("Labour", 1400), ("Consumables", 800)],
             total=8500, brand=(120, 90, 30)),
    ]
    out = []
    for i, s in enumerate(specs):
        img = crisis_bill(title=s["title"], vendor=s["vendor"], lines=s["lines"],
                          total=s["total"], stamp=s["stamp"], brand=s["brand"])
        img = photograph(img, seed=200 + i, harsh=True)
        path = EVID / f"{s['key']}.jpg"
        save_jpeg(img, path, quality=74)
        out.append({"path": path, **s})
    return out
