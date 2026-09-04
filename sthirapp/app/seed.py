"""Seeds Sthir with four gig/informal-worker personas and 12 weeks of income.

Personas are chosen to span the demo's whole argument:

  Ravi     Swiggy/Zomato delivery rider, Chennai. Income is DECLINING and he
           carries moneylender debt at 60% APR - the distress + debt-spiral
           case. Verified platform payouts (tier A).
  Lakshmi  Job-work tailor, Tiruppur. Lumpy invoice income, one large invoice
           now OVERDUE - keeps the e-invoice QR + Tanglish showcase and drives
           the payout-overdue alert.
  Arun     Freelance designer, Bengaluru. Healthy, diversified client income -
           the "resilient" contrast that proves the score discriminates.
  Meena    Domestic worker, cash gigs only. Low digital literacy, unverifiable
           income (tier C) - the inclusion case.

Depth matters: 12 weeks of weekly income per worker so volatility, runway and
trend are computed from real series, not a single point.
"""
from __future__ import annotations

import datetime as dt
import random

from .config import INVOICE_DIR
from .db import Base, SessionLocal, engine
from .models import (Advance, Alert, Allocation, Event, IncomeEvent,
                     IncomeSource, Obligation, SavingsTxn, Worker)
from .services import einvoice_qr as qr
from .services import phash
from .services.gstin import make_valid
from .services.income_verify import register_platform_payout
from . import make_invoices as mk
from . import make_evidence as me

TODAY = dt.date(2026, 9, 3)


def _dtc(d: dt.date) -> dt.datetime:
    return dt.datetime.combine(d, dt.time(12, 0))


# --------------------------------------------------------------- sources

SOURCES = [
    dict(key="swiggy", name="Swiggy", kind="gig_platform",
         observed=48, on_time=47, late=2.0),
    dict(key="zomato", name="Zomato", kind="gig_platform",
         observed=40, on_time=38, late=3.5),
    dict(key="andal", name="Sree Andal Exports", kind="buyer",
         gstin=make_valid("33AAECS5678K1Z"), observed=4, on_time=4, late=0.0),
    dict(key="kovai", name="Kovai Knits Pvt Ltd", kind="buyer",
         gstin=make_valid("33AADCK3344M1Z"), observed=3, on_time=1, late=28.0),
    dict(key="brightspace", name="BrightSpace Design Studio", kind="client",
         gstin=make_valid("29AAGCB9911Q1Z"), observed=9, on_time=8, late=6.0),
    dict(key="pixelworks", name="PixelWorks Media", kind="client",
         gstin=make_valid("29AACCP2244R1Z"), observed=5, on_time=5, late=0.0),
    dict(key="household", name="Private households (cash)", kind="employer",
         observed=0, on_time=0, late=0.0),
]


def _weekly_series(base: float, trend: float, jitter: float, n: int,
                   rng: random.Random) -> list[float]:
    """A weekly income series with a linear trend and multiplicative jitter."""
    out = []
    for i in range(n):
        level = base * (1 + trend * i / n)
        val = max(0, level * (1 + rng.uniform(-jitter, jitter)))
        out.append(round(val, -1))
    return out


def run(reset: bool = True) -> dict:
    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    rng = random.Random(11)

    src = {}
    for s in SOURCES:
        row = IncomeSource(name=s["name"], kind=s["kind"], gstin=s.get("gstin"),
                           payments_observed=s["observed"],
                           payments_on_time=s["on_time"], avg_days_late=s["late"])
        db.add(row); db.flush(); src[s["key"]] = row

    workers: dict[str, Worker] = {}
    n_income = 0

    def add_worker(**kw):
        w = Worker(**kw); db.add(w); db.flush(); workers[kw["phone"]] = w
        return w

    def weekly_events(worker, source, series, kind, ref_prefix,
                      received=True, tier_default="A"):
        nonlocal n_income
        n = len(series)
        for i, amt in enumerate(series):
            if amt <= 0:
                continue
            wk_end = TODAY - dt.timedelta(weeks=(n - 1 - i))
            db.add(IncomeEvent(
                worker_id=worker.id, source_id=source.id, kind=kind,
                reference=f"{ref_prefix}-{1000 + i}", gross=amt,
                expected_date=_dtc(wk_end), received_date=_dtc(wk_end) if received else None,
                tier=tier_default, verified=(tier_default == "A"),
                confidence=0.97 if tier_default == "A" else 0.6,
                status="received" if received else "expected"))
            n_income += 1

    def obligations(worker, lines):
        for ln in lines:
            db.add(Obligation(worker_id=worker.id, **ln))

    # ---------------- Ravi: declining delivery rider, in distress ----------
    ravi = add_worker(name="Ravi Kumar", phone="+919600012345", city="Chennai",
                      occupation="Delivery rider", gig_types=["swiggy", "zomato"],
                      languages=["ta", "en"], digital_literacy="medium",
                      cash_buffer=1200, savings_balance=0, autosave_rate=0.10)
    # income falling ~35% over 12 weeks - the early-distress signal
    s_sw = _weekly_series(5200, trend=-0.38, jitter=0.16, n=12, rng=rng)
    s_zo = _weekly_series(2100, trend=-0.30, jitter=0.22, n=12, rng=rng)
    weekly_events(ravi, src["swiggy"], s_sw, "gig_payout", "SWGY-PO")
    weekly_events(ravi, src["zomato"], s_zo, "gig_payout", "ZOM-PO")
    obligations(ravi, [
        dict(head="Room rent", payee="Landlord", category="rent", monthly=6500, priority=1),
        dict(head="Bike EMI", payee="Hero Fincorp", category="emi", monthly=3200, priority=2, is_debt=True, apr=0.24),
        dict(head="Moneylender", payee="Local financier", category="emi", monthly=4200, priority=2, is_debt=True, apr=0.60),
        dict(head="Food & essentials", payee="Self", category="essential", monthly=6000, priority=1),
        dict(head="Phone & data", payee="Airtel", category="utility", monthly=400, priority=3),
        dict(head="Family (parents)", payee="Family", category="family", monthly=3000, priority=4),
    ])

    # ---------------- Lakshmi: tailor with lumpy invoice income ------------
    lakshmi = add_worker(name="Lakshmi Devi", phone="+919843055512", city="Tiruppur",
                         occupation="Job-work tailor", gig_types=["job_work"],
                         languages=["ta", "en"], digital_literacy="low",
                         cash_buffer=8000, savings_balance=3000, autosave_rate=0.12)
    # lumpy: income in weeks she delivers a consignment, zero otherwise
    lak_series = [0, 0, 42000, 0, 0, 0, 38000, 0, 0, 55000, 0, 0]
    weekly_events(lakshmi, src["andal"], lak_series, "invoice", "INV", tier_default="A")
    obligations(lakshmi, [
        dict(head="Rent (unit + home)", payee="Landlord", category="rent", monthly=9000, priority=1),
        dict(head="Yarn & thread", payee="Amman Yarns", category="input", monthly=14000, priority=2),
        dict(head="Helper wages", payee="2 helpers", category="essential", monthly=16000, priority=1),
        dict(head="Power (TNEB)", payee="TNEB", category="utility", monthly=3500, priority=3),
        dict(head="Food & essentials", payee="Self", category="essential", monthly=8000, priority=1),
    ])

    # ---------------- Arun: healthy freelance designer --------------------
    arun = add_worker(name="Arun Prakash", phone="+919611122334", city="Bengaluru",
                      occupation="Freelance designer", gig_types=["freelance"],
                      languages=["en", "ta"], digital_literacy="high",
                      cash_buffer=48000, savings_balance=120000, autosave_rate=0.20)
    weekly_events(arun, src["brightspace"],
                  _weekly_series(11000, trend=0.05, jitter=0.35, n=12, rng=rng),
                  "invoice", "BS-INV", tier_default="A")
    weekly_events(arun, src["pixelworks"],
                  _weekly_series(6000, trend=0.10, jitter=0.5, n=12, rng=rng),
                  "invoice", "PW-INV", tier_default="A")
    obligations(arun, [
        dict(head="Rent", payee="Landlord", category="rent", monthly=22000, priority=1),
        dict(head="Software subs", payee="Adobe", category="input", monthly=4000, priority=3),
        dict(head="Food & essentials", payee="Self", category="essential", monthly=15000, priority=1),
        dict(head="SIP investment", payee="Mutual fund", category="savings", monthly=10000, priority=8),
    ])

    # ---------------- Meena: cash-only domestic worker --------------------
    meena = add_worker(name="Meena Bai", phone="+919600098765", city="Chennai",
                       occupation="Domestic worker", gig_types=["household"],
                       languages=["ta"], digital_literacy="low",
                       cash_buffer=900, savings_balance=0, autosave_rate=0.08)
    weekly_events(meena, src["household"],
                  _weekly_series(3200, trend=-0.05, jitter=0.12, n=12, rng=rng),
                  "cash_gig", "CASH", tier_default="C")
    obligations(meena, [
        dict(head="Room rent", payee="Landlord", category="rent", monthly=4500, priority=1),
        dict(head="Food & essentials", payee="Self", category="essential", monthly=5500, priority=1),
        dict(head="Chit fund", payee="Local chit", category="savings", monthly=1000, priority=6),
    ])

    # ---------------- live demo evidence documents ------------------------
    # 1) Ravi's latest Swiggy payout screenshot (verified platform payout)
    payout_specs = [
        dict(platform="Swiggy", rider="Ravi Kumar", payout_id="SWGY-PO-88231",
             amount=3180, orders=94, week="25 Aug - 31 Aug 2026", brand=(252, 100, 30)),
        dict(platform="Zomato", rider="Ravi Kumar", payout_id="ZOM-PO-51120",
             amount=1240, orders=38, week="25 Aug - 31 Aug 2026", brand=(226, 55, 65)),
    ]
    payouts = me.build(payout_specs)
    me.build_crises()  # seed the Crisis Override demo bills
    for p in payouts:
        register_platform_payout(p["payout_id"])   # platform confirms -> tier A

    # 2) Lakshmi's newest e-invoice (signed QR) - large, and it will be OVERDUE
    inv_spec = dict(
        mill={"name": "Lakshmi Devi Tailoring", "gstin": make_valid("33ADHPL7788Q1Z"),
              "address": "12 Kumaran Rd, Tiruppur"},
        buyer={"name": "Kovai Knits Pvt Ltd", "gstin": make_valid("33AADCK3344M1Z"),
               "address": "Coimbatore"},
        number="INV-9007", printed_amount=180000, qr_amount=180000,
        issue_date=dt.date(2026, 6, 10), due_date=dt.date(2026, 7, 25),
        terms=45, items=[{"desc": "Stitched cotton kurtis", "hsn": "6106",
                          "qty": 900, "rate": 200.0, "amount": 180000.0}],
        words="Rupees One Lakh Eighty Thousand Only")
    img = mk.render_tier_a(inv_spec)
    img = mk.photograph(img, seed=71)
    inv_path = INVOICE_DIR / "inv-9007.jpg"
    mk.save_jpeg(img, inv_path)

    # persist those two as demo income events (Ravi's already-received via history;
    # these are the "submit live" ones)
    ravi_live = IncomeEvent(
        worker_id=ravi.id, source_id=src["swiggy"].id, kind="gig_payout",
        reference="SWGY-PO-88231", gross=3180, expected_date=_dtc(TODAY),
        received_date=None, tier="C", status="expected",
        evidence_path=str(payouts[0]["path"]),
        evidence_phash=phash.combined(payouts[0]["path"]))
    db.add(ravi_live)

    lakshmi_overdue = IncomeEvent(
        worker_id=lakshmi.id, source_id=src["kovai"].id, kind="invoice",
        reference="INV-9007", gross=180000,
        expected_date=_dtc(dt.date(2026, 7, 25)), received_date=None,
        tier="C", status="expected", evidence_path=str(inv_path),
        evidence_phash=phash.combined(inv_path))
    db.add(lakshmi_overdue)
    n_income += 2

    db.add(Event(type="seed.completed", subject_id="system",
                 payload={"workers": len(workers), "sources": len(SOURCES),
                          "income_events": n_income}))
    db.commit()

    ids = {w.occupation: w.id for w in workers.values()}
    db.close()
    return {"workers": len(workers), "sources": len(SOURCES),
            "income_events": n_income, "worker_ids": ids,
            "live_docs": {"ravi_payout": ravi_live.id if False else "SWGY-PO-88231",
                          "lakshmi_invoice": "INV-9007"}}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, default=str))
