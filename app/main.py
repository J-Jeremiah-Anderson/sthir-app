"""Sthir API - income resilience for gig & informal workers.

Decoupled backend: the frontend is built separately and speaks HTTP/JSON only.
CORS is open in dev; interactive docs at /docs; the integration contract is in
API.md. Every money figure is a mock/simulation for the prototype.
"""
from __future__ import annotations

import datetime as dt
import shutil
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import DATA_DIR
from .db import get_db
from .models import (Advance, Alert, Allocation, Event, IncomeEvent,
                     IncomeSource, Obligation, SavingsTxn, Worker)
from .schemas import ClarifyAnswer
from .services import crisis, distress, entitlements, resilience, sources
from .services.pipeline import (process_income, request_advance,
                                snapshot_and_alerts)

DEMO_TODAY = dt.date(2026, 9, 3)

app = FastAPI(title="Sthir API", version="1.0.0",
              description="Income resilience & early-distress prevention for "
                          "gig and informal workers. See API.md.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])


# ------------------------------------------------------------- serializers

def worker_card(w: Worker) -> dict:
    return {"id": w.id, "name": w.name, "phone": w.phone, "city": w.city,
            "occupation": w.occupation, "gig_types": w.gig_types,
            "languages": w.languages, "digital_literacy": w.digital_literacy,
            "cash_buffer": w.cash_buffer, "savings_balance": w.savings_balance,
            "autosave_rate": w.autosave_rate}


def income_out(e: IncomeEvent) -> dict:
    return {"id": e.id, "reference": e.reference, "kind": e.kind,
            "gross": e.gross, "tier": e.tier, "verified": e.verified,
            "status": e.status,
            "expected_date": e.expected_date.date().isoformat() if e.expected_date else None,
            "received_date": e.received_date.date().isoformat() if e.received_date else None,
            "source_name": e.source.name if e.source else None,
            "confidence": e.confidence,
            "evidence_url": f"/api/income/{e.id}/evidence" if e.evidence_path else None,
            "advance": {"id": e.advance.id, "net": e.advance.net_disbursed}
                       if e.advance else None}


# ------------------------------------------------------------- meta

@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    return {"status": "ok", "demo_today": DEMO_TODAY.isoformat(),
            "workers": db.scalar(select(func.count()).select_from(Worker)),
            "income_events": db.scalar(select(func.count()).select_from(IncomeEvent))}


# ------------------------------------------------------------- workers

@app.get("/api/workers")
def list_workers(db: Session = Depends(get_db)):
    out = []
    for w in db.execute(select(Worker)).scalars():
        card = worker_card(w)
        snap = resilience.compute(db, w, DEMO_TODAY)
        card["resilience_score"] = snap["resilience_score"]
        card["resilience_band"] = snap["resilience_band"]
        card["open_alerts"] = len(distress.assess(db, w, snap, DEMO_TODAY))
        out.append(card)
    return out


@app.get("/api/workers/{worker_id}")
def get_worker(worker_id: str, db: Session = Depends(get_db)):
    w = db.get(Worker, worker_id)
    if not w:
        raise HTTPException(404, "worker not found")
    sa = snapshot_and_alerts(db, w, DEMO_TODAY)
    return {**worker_card(w), **sa,
            "income_events": [income_out(e) for e in
                              sorted(w.income_events, key=lambda x: x.expected_date or dt.datetime.min, reverse=True)][:20]}


@app.get("/api/workers/{worker_id}/resilience")
def worker_resilience(worker_id: str, db: Session = Depends(get_db)):
    w = db.get(Worker, worker_id)
    if not w:
        raise HTTPException(404, "worker not found")
    return resilience.compute(db, w, DEMO_TODAY)


@app.get("/api/workers/{worker_id}/alerts")
def worker_alerts(worker_id: str, db: Session = Depends(get_db)):
    w = db.get(Worker, worker_id)
    if not w:
        raise HTTPException(404, "worker not found")
    snap = resilience.compute(db, w, DEMO_TODAY)
    return distress.assess(db, w, snap, DEMO_TODAY)


@app.get("/api/workers/{worker_id}/income")
def worker_income(worker_id: str, db: Session = Depends(get_db)):
    rows = db.execute(select(IncomeEvent).where(IncomeEvent.worker_id == worker_id)
                      .order_by(IncomeEvent.expected_date.desc())).scalars()
    return [income_out(e) for e in rows]


@app.get("/api/workers/{worker_id}/savings")
def worker_savings(worker_id: str, db: Session = Depends(get_db)):
    w = db.get(Worker, worker_id)
    if not w:
        raise HTTPException(404, "worker not found")
    txns = db.execute(select(SavingsTxn).where(SavingsTxn.worker_id == worker_id)
                      .order_by(SavingsTxn.ts.desc())).scalars()
    return {"balance": w.savings_balance, "autosave_rate": w.autosave_rate,
            "transactions": [{"direction": t.direction, "amount": t.amount,
                              "reason": t.reason, "balance_after": t.balance_after,
                              "ts": t.ts.isoformat()} for t in txns]}


# ------------------------------------------------------------- income actions

@app.get("/api/samples")
def samples(db: Session = Depends(get_db)):
    """The 'submit live' demo income events (a payout screenshot, an invoice)
    that are still pending verification."""
    rows = db.execute(select(IncomeEvent).where(IncomeEvent.status == "expected",
                      IncomeEvent.evidence_path.isnot(None))).scalars()
    return [{"income_event_id": e.id, "reference": e.reference, "kind": e.kind,
             "worker_id": e.worker_id, "worker_name": e.worker.name,
             "gross": e.gross, "evidence_url": f"/api/income/{e.id}/evidence"}
            for e in rows]


@app.post("/api/income/{event_id}/verify")
def verify_income_ep(event_id: str, db: Session = Depends(get_db)):
    """Run verification + resilience refresh on a submitted income event.
    Primary demo action: 'I got paid / I have an invoice'."""
    e = db.get(IncomeEvent, event_id)
    if not e:
        raise HTTPException(404, "income event not found")
    return process_income(db, e, as_of=DEMO_TODAY)


@app.post("/api/workers/{worker_id}/submit-income")
async def submit_income(worker_id: str, file: UploadFile = File(...),
                        kind: str = Form("gig_payout"),
                        gross: float = Form(...), reference: str = Form(""),
                        db: Session = Depends(get_db)):
    """Upload a fresh payout screenshot / invoice photo and process it."""
    from .services import phash
    from .services.extract import extract
    w = db.get(Worker, worker_id)
    if not w:
        raise HTTPException(404, "worker not found")
    dest = DATA_DIR / "evidence" / f"up-{uuid.uuid4().hex[:8]}-{file.filename}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    # Read the image once. When a real vision model looked at it, use its verdict
    # to reject images that are not genuine proof of income (selfies, memes, ...)
    # instead of booking them as verified income.
    try:
        extraction, backend = extract(str(dest))
    except Exception:
        extraction, backend = None, "unreadable"
    if (extraction is not None and backend in ("gemini", "claude")
            and not getattr(extraction, "is_income_document", True)):
        dest.unlink(missing_ok=True)
        db.add(Event(type="income.rejected", subject_id=worker_id,
                     payload={"reason": "not_income_document", "backend": backend}))
        db.commit()
        return {
            "rejected": True, "reason_code": "not_income",
            "backend": backend,
            "document_type": extraction.document_type,
            "confidence": extraction.overall_confidence,
            "reason": ("This image does not look like a proof of income "
                       "(invoice, payout screenshot, receipt or payslip). "
                       "Please upload a valid income document."),
        }

    # Cross-verify the CLAIMED amount against the amount the model read off the
    # proof. If the worker types a figure the document does not support, reject
    # it - the proof is the source of truth, not the typed number.
    read = getattr(extraction, "total_amount", None) if extraction else None
    if (read is not None and read > 0 and backend in ("gemini", "claude")):
        rel = abs(gross - read) / max(read, gross)
        if rel > 0.12 and abs(gross - read) > 50:
            dest.unlink(missing_ok=True)
            db.add(Event(type="income.rejected", subject_id=worker_id,
                         payload={"reason": "amount_mismatch", "claimed": gross,
                                  "document_amount": read, "backend": backend}))
            db.commit()
            return {
                "rejected": True, "reason_code": "amount_mismatch",
                "backend": backend, "claimed": gross, "document_amount": read,
                "reason": (f"The amount you entered (Rs {gross:,.0f}) does not "
                           f"match the amount on the document (Rs {read:,.0f}). "
                           f"Enter the amount shown on the proof."),
            }

    try:
        ph = phash.combined(dest)
    except Exception:
        ph = None  # unreadable/corrupt upload - still accept, just no dedupe hash

    # Double-financing guard: reject a proof that is the same (or a
    # re-photograph) of one already submitted - by this worker OR any other.
    if ph:
        prior = db.execute(select(IncomeEvent).where(
            IncomeEvent.evidence_phash.isnot(None))).scalars().all()
        for p in prior:
            dup, dist = phash.is_duplicate(ph, p.evidence_phash)
            if dup:
                same_worker = (p.worker_id == worker_id)
                dest.unlink(missing_ok=True)
                db.add(Event(type="fraud.duplicate_proof", subject_id=worker_id,
                             payload={"matched_income": p.id, "distance": dist,
                                      "same_worker": same_worker}))
                db.commit()
                return {
                    "rejected": True, "reason_code": "duplicate_proof",
                    "backend": backend, "distance": dist,
                    "same_worker": same_worker,
                    "reason": ("This proof has already been submitted"
                               + ("" if same_worker else " (by another worker)")
                               + ". The same payout can't be financed twice."),
                }

    e = IncomeEvent(worker_id=worker_id, kind=kind, gross=gross,
                    reference=reference or f"UP-{uuid.uuid4().hex[:5].upper()}",
                    expected_date=dt.datetime.combine(DEMO_TODAY, dt.time()),
                    status="expected", evidence_path=str(dest),
                    evidence_phash=ph)
    db.add(e); db.flush()
    return process_income(db, e, as_of=DEMO_TODAY,
                          precomputed=(extraction, backend))


# --- v1.2: read-then-confirm flow (make the AI extraction visible) ----------
import time as _time  # noqa: E402
_PREVIEW: dict = {}   # token -> {path, extraction, backend, worker_id, ts}


def _purge_previews():
    now = _time.time()
    for k in [k for k, v in _PREVIEW.items() if now - v["ts"] > 600]:
        v = _PREVIEW.pop(k, None)
        try:
            if v:
                Path(v["path"]).unlink(missing_ok=True)
        except Exception:
            pass


def _find_duplicate(db: Session, ph: str):
    """Return (matched_event, distance) for a duplicate proof, else (None, None)."""
    from .services import phash
    for p in db.execute(select(IncomeEvent).where(
            IncomeEvent.evidence_phash.isnot(None))).scalars().all():
        dup, dist = phash.is_duplicate(ph, p.evidence_phash)
        if dup:
            return p, dist
    return None, None


@app.post("/api/workers/{worker_id}/extract-preview")
async def extract_preview(worker_id: str, file: UploadFile = File(...),
                          db: Session = Depends(get_db)):
    """Read a photo with the vision model WITHOUT booking income - so the
    worker can see what was read and confirm the amount."""
    from .services.extract import extract
    if not db.get(Worker, worker_id):
        raise HTTPException(404, "worker not found")
    _purge_previews()
    dest = DATA_DIR / "evidence" / f"pv-{uuid.uuid4().hex[:8]}-{file.filename}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        extraction, backend = extract(str(dest))
    except Exception:
        extraction, backend = None, "unreadable"
    if (extraction is not None and backend in ("gemini", "claude")
            and not getattr(extraction, "is_income_document", True)):
        dest.unlink(missing_ok=True)
        return {"rejected": True, "reason_code": "not_income", "backend": backend,
                "reason": ("This image does not look like a proof of income. "
                           "Please upload a valid income document.")}
    token = uuid.uuid4().hex
    _PREVIEW[token] = {"path": str(dest), "extraction": extraction,
                       "backend": backend, "worker_id": worker_id,
                       "ts": _time.time()}
    return {
        "token": token, "backend": backend,
        "read_amount": getattr(extraction, "total_amount", None) if extraction else None,
        "source_name": getattr(extraction, "seller_name", None) if extraction else None,
        "document_type": getattr(extraction, "document_type", None) if extraction else None,
        "date": getattr(extraction, "invoice_date", None) if extraction else None,
        "confidence": getattr(extraction, "overall_confidence", None) if extraction else None,
        "evidence_url": f"/api/preview/{token}/evidence",
    }


@app.get("/api/preview/{token}/evidence")
def preview_evidence(token: str):
    v = _PREVIEW.get(token)
    if not v or not Path(v["path"]).exists():
        raise HTTPException(404, "preview expired")
    return FileResponse(v["path"], media_type="image/jpeg")


@app.post("/api/workers/{worker_id}/confirm-income")
async def confirm_income(worker_id: str, token: str = Form(...),
                         gross: float = Form(...), kind: str = Form("gig_payout"),
                         reference: str = Form(""),
                         db: Session = Depends(get_db)):
    """Book the previewed income at the amount the worker confirmed. Re-uses the
    extraction from the preview (no second model call) and still enforces the
    amount cross-check and duplicate guard."""
    from .services import phash
    v = _PREVIEW.get(token)
    if not v or v["worker_id"] != worker_id or not Path(v["path"]).exists():
        raise HTTPException(400, "preview expired - please upload again")
    dest = Path(v["path"])
    extraction, backend = v["extraction"], v["backend"]

    read = getattr(extraction, "total_amount", None) if extraction else None
    if read is not None and read > 0 and backend in ("gemini", "claude"):
        rel = abs(gross - read) / max(read, gross)
        if rel > 0.12 and abs(gross - read) > 50:
            return {"rejected": True, "reason_code": "amount_mismatch",
                    "backend": backend, "claimed": gross, "document_amount": read,
                    "reason": (f"The amount you confirmed (Rs {gross:,.0f}) does "
                               f"not match the document (Rs {read:,.0f}).")}
    try:
        ph = phash.combined(dest)
    except Exception:
        ph = None
    if ph:
        match, dist = _find_duplicate(db, ph)
        if match is not None:
            _PREVIEW.pop(token, None)
            dest.unlink(missing_ok=True)
            same = (match.worker_id == worker_id)
            db.add(Event(type="fraud.duplicate_proof", subject_id=worker_id,
                         payload={"matched_income": match.id, "distance": dist,
                                  "same_worker": same}))
            db.commit()
            return {"rejected": True, "reason_code": "duplicate_proof",
                    "backend": backend, "distance": dist, "same_worker": same,
                    "reason": ("This proof has already been submitted"
                               + ("" if same else " (by another worker)")
                               + ". The same payout can't be financed twice.")}
    _PREVIEW.pop(token, None)
    e = IncomeEvent(worker_id=worker_id, kind=kind, gross=gross,
                    reference=reference or f"UP-{uuid.uuid4().hex[:5].upper()}",
                    expected_date=dt.datetime.combine(DEMO_TODAY, dt.time()),
                    status="expected", evidence_path=str(dest), evidence_phash=ph)
    db.add(e); db.flush()
    return process_income(db, e, as_of=DEMO_TODAY, precomputed=(extraction, backend))


@app.get("/api/income/{event_id}/evidence")
def income_evidence(event_id: str, db: Session = Depends(get_db)):
    e = db.get(IncomeEvent, event_id)
    if not e or not e.evidence_path or not Path(e.evidence_path).exists():
        raise HTTPException(404, "evidence not found")
    return FileResponse(e.evidence_path, media_type="image/jpeg")


@app.post("/api/income/{event_id}/advance")
def advance(event_id: str, requested: float | None = None,
            db: Session = Depends(get_db)):
    """Request a responsible smoothing advance against a verified income event.
    May return decision=refused - that is the consumer-protection path."""
    e = db.get(IncomeEvent, event_id)
    if not e:
        raise HTTPException(404, "income event not found")
    return request_advance(db, e, requested=requested, as_of=DEMO_TODAY)


@app.post("/api/income/{event_id}/clarify")
def clarify(event_id: str, answer: ClarifyAnswer, db: Session = Depends(get_db)):
    e = db.get(IncomeEvent, event_id)
    if not e:
        raise HTTPException(404, "income event not found")
    if answer.field == "amount":
        try:
            e.gross = float(str(answer.value).replace(",", ""))
        except ValueError:
            raise HTTPException(400, "amount not a number")
    e.confidence = 0.95
    db.add(Event(type="clarify.answered", subject_id=e.id,
                 payload={"field": answer.field, "value": answer.value}))
    db.flush()
    return process_income(db, e, as_of=DEMO_TODAY)


@app.get("/api/income/{event_id}/exposure")
def income_exposure(event_id: str, db: Session = Depends(get_db)):
    """For invoice-type income: the client's statutory late-payment exposure
    (MSMED s.16 + s.43B(h)) - real leverage for an informal supplier."""
    e = db.get(IncomeEvent, event_id)
    if not e or e.kind != "invoice" or not e.expected_date:
        raise HTTPException(404, "not an invoice with a due date")
    return sources.statutory_exposure(amount=e.gross,
                                      due_date=e.expected_date.date(),
                                      as_of=DEMO_TODAY)


# ------------------------------------------------------------- sources

@app.get("/api/sources")
def list_sources(db: Session = Depends(get_db)):
    return [sources.scorecard(db, s) for s in db.execute(select(IncomeSource)).scalars()]


@app.post("/api/sources/{source_id}/flag")
def flag_source(source_id: str, worker_id: str = Form(...),
                db: Session = Depends(get_db)):
    s = db.get(IncomeSource, source_id)
    if not s:
        raise HTTPException(404, "source not found")
    flags = set(s.flagged_by_workers or [])
    flags.add(worker_id)
    s.flagged_by_workers = list(flags)
    db.add(Event(type="source.flagged", subject_id=source_id,
                 payload={"worker_id": worker_id, "total": len(flags)}))
    db.commit()
    return {"source_id": source_id, "flags": len(flags), "amber": len(flags) >= 2}


# ------------------------------------------------------------- bank console

@app.get("/api/portfolio")
def portfolio(db: Session = Depends(get_db)):
    """Bank-facing view: population resilience, distress caseload, advances,
    fraud rings. This is the judges' 'institutional' screen."""
    workers = db.execute(select(Worker)).scalars().all()
    bands = {"critical": 0, "fragile": 0, "building": 0, "resilient": 0}
    distress_cases = []
    for w in workers:
        snap = resilience.compute(db, w, DEMO_TODAY)
        bands[snap["resilience_band"]] += 1
        al = distress.assess(db, w, snap, DEMO_TODAY)
        crit = [a for a in al if a["severity"] == "critical"]
        if crit:
            distress_cases.append({"worker_id": w.id, "name": w.name,
                                   "occupation": w.occupation,
                                   "resilience_score": snap["resilience_score"],
                                   "runway_days": snap["runway_days"],
                                   "top_alert": crit[0]["title"]})
    advances = db.execute(select(Advance).where(Advance.status == "disbursed")).scalars().all()
    refused = db.scalar(select(func.count()).select_from(Event)
                        .where(Event.type == "advance.refused")) or 0
    return {
        "workers": len(workers),
        "resilience_bands": bands,
        "distress_cases": sorted(distress_cases, key=lambda x: x["resilience_score"]),
        "advances_disbursed": len(advances),
        "advances_value": round(sum(a.advance_amount for a in advances), 2),
        "advances_refused_protectively": refused,
        "fraud_rings": sources.find_rings(db),
    }


# ------------------------------------------------------------- crisis & welfare

@app.get("/api/workers/{worker_id}/entitlements")
def worker_entitlements(worker_id: str, crisis_tag: str | None = None,
                        amount: float | None = None, db: Session = Depends(get_db)):
    """Government schemes this worker is eligible for. With a crisis_tag and
    amount, returns the welfare offset - how much free coverage replaces a loan."""
    w = db.get(Worker, worker_id)
    if not w:
        raise HTTPException(404, "worker not found")
    return entitlements.match(w, crisis_tag=crisis_tag, amount=amount)


@app.post("/api/workers/{worker_id}/crisis-override")
async def crisis_override(worker_id: str, file: UploadFile = File(...),
                          requested: float | None = Form(None),
                          db: Session = Depends(get_db)):
    """Emergency bypass: classify the bill/damage photo, try the Entitlement
    Bridge first, then disburse any residual DIRECT TO THE VENDOR over UPI."""
    from .services import phash
    w = db.get(Worker, worker_id)
    if not w:
        raise HTTPException(404, "worker not found")
    dest = DATA_DIR / "evidence" / f"crisis-{uuid.uuid4().hex[:8]}-{file.filename}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return crisis.handle(db, w, dest, requested=requested)


@app.post("/api/workers/{worker_id}/crisis-override/sample/{key}")
def crisis_override_sample(worker_id: str, key: str, requested: float | None = None,
                           db: Session = Depends(get_db)):
    """Run Crisis Override against a seeded demo bill: 'accident' or 'hospital'."""
    w = db.get(Worker, worker_id)
    if not w:
        raise HTTPException(404, "worker not found")
    path = DATA_DIR / "evidence" / f"crisis-{key}.jpg"
    if not path.exists():
        raise HTTPException(404, f"no sample crisis '{key}'")
    return crisis.handle(db, w, path, requested=requested)


# ------------------------------------------------------------- events / reset

@app.get("/api/events")
def events(limit: int = 60, db: Session = Depends(get_db)):
    rows = db.execute(select(Event).order_by(Event.ts.desc()).limit(limit)).scalars()
    return [{"ts": e.ts.isoformat(), "type": e.type, "subject_id": e.subject_id,
             "payload": e.payload} for e in rows]


@app.post("/api/demo/reset")
def reset():
    from .seed import run
    return {"reset": True, **run(reset=True)}


# ------------------------------------------------------------- static frontend
# Serves the two portal apps (web/worker.html, web/lender.html, web/index.html)
# from the same origin as the API, so a single deploy ships everything.
from fastapi.staticfiles import StaticFiles  # noqa: E402


class _NoCacheStatic(StaticFiles):
    """Serve HTML with no-cache so a redeploy is always picked up immediately
    (prevents browsers white-screening on a stale cached page after a fix)."""
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if str(path).endswith(".html") or path in ("", "/", "."):
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp


_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if _WEB_DIR.is_dir():
    app.mount("/", _NoCacheStatic(directory=str(_WEB_DIR), html=True), name="web")
