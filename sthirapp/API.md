# Sthir API — Frontend Integration Contract

Base URL (dev): `http://localhost:8000`  •  Interactive docs: `/docs`  •  OpenAPI: `/openapi.json`

All responses are JSON. CORS is open in dev. Every monetary figure is a prototype simulation.
The backend runs on a fixed demo clock: **2026-09-03** (so late-payment / runway math is stable).

## Auth
None in the prototype. The "logged-in worker" is chosen client-side from `GET /api/workers`.
(Production would add OTP login; the frontend should keep a single `workerId` in app state.)

## Conventions
- IDs are opaque strings (`wrk_…`, `inc_…`, `adv_…`). Never parse them.
- Money is a number in INR (rupees). Format client-side as `₹1,23,456`.
- Images are served as `image/jpeg` from the `*_url` fields — use them directly as `<img src>`.

---

## Endpoints

### Workers
| Method | Path | Returns |
|---|---|---|
| GET | `/api/workers` | List of workers with `resilience_score`, `resilience_band`, `open_alerts`. Home/switcher. |
| GET | `/api/workers/{id}` | Full profile + `resilience` snapshot + `alerts` + recent `income_events`. The main worker screen. |
| GET | `/api/workers/{id}/resilience` | Just the resilience snapshot (score, sub-scores, volatility, runway, weekly series, obligations). |
| GET | `/api/workers/{id}/alerts` | Distress alerts, each with a graded `intervention.ladder`. |
| GET | `/api/workers/{id}/income` | Full income history (weekly events). Feeds the income chart + list. |
| GET | `/api/workers/{id}/savings` | Savings balance + sweep/drawdown transaction ledger. |

### Income actions (the core flow)
| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/samples` | — | The "submit live" demo income events (payout screenshot, invoice) still pending. |
| POST | `/api/income/{id}/verify` | — | Runs verification + resilience refresh. `{verification, resilience, alerts}`. |
| POST | `/api/workers/{id}/submit-income` | multipart: `file`, `kind`, `gross`, `reference` | Upload a fresh photo → verify + process. |
| POST | `/api/income/{id}/advance` | `?requested=` (optional) | Responsible-credit decision. `decision` may be `approved`, `refused` (protective!), or `clarify`. On approval includes `allocation` waterfall + `resilience_before/after`. |
| POST | `/api/income/{id}/clarify` | `{invoice_id,field,value}` | Answer the one clarifying question, then re-decide. |
| GET | `/api/income/{id}/evidence` | — | The evidence image (JPEG). |
| GET | `/api/income/{id}/exposure` | — | (invoice-type only) Client's statutory late-payment exposure (MSMED s.16 + s.43B(h)). |

### Sources & bank console
| Method | Path | Returns |
|---|---|---|
| GET | `/api/sources` | Income-source scorecards (reliability grade, avg days late, shared flags). |
| POST | `/api/sources/{id}/flag` | Form `worker_id`. Shared reputation — 2 flags = amber. |
| GET | `/api/portfolio` | Bank-facing: resilience bands, distress caseload, advances, protective refusals, fraud rings. |

### Ops
| Method | Path | Notes |
|---|---|---|
| GET | `/api/events` | Append-only event log (`?limit=`). Nice for a live activity feed. |
| POST | `/api/demo/reset` | Drops, reseeds, replays to a known state in <2s. Wire to a hidden reset button. |
| GET | `/api/health` | Liveness + counts. |

---

## Key response shapes (abridged)

`GET /api/workers/{id}` →
```json
{ "id":"wrk_…","name":"Ravi Kumar","occupation":"Delivery rider","city":"Chennai",
  "languages":["ta","en"],"digital_literacy":"medium",
  "cash_buffer":1200,"savings_balance":0,
  "resilience": { "resilience_score":33,"resilience_band":"fragile","volatility_index":0.336,
    "income_trend_4w":-0.16,"runway_days":1.5,"monthly_income_est":24919,
    "weekly_income":[...12 numbers...],"sub_scores":{"runway":5,"savings":0,"stability":60,"coverage":82},
    "obligations":{"total":23300,"essential":15900,"debt_service":7400,"lines":[…]} },
  "alerts":[ { "severity":"critical","code":"debt_spiral","title":"Debt service is 30% of income",
    "detail":"…60% APR — a classic debt trap.","intervention":{"ladder":[{"step":"refinance","label":"…"}]} } ],
  "income_events":[ {"id":"inc_…","kind":"gig_payout","gross":3180,"tier":"A","status":"received",…} ] }
```

`POST /api/income/{id}/advance` (refused) →
```json
{ "decision": { "decision":"refused","protective":true,
    "refuse_reason":"You are repaying Moneylender at 60% APR…",
    "alternative":"refinance_high_interest_debt",
    "alternative_label":"Refinance Moneylender (currently 60% APR)",
    "reasons":[{"code":"predatory_debt","label":"…","ok":false}] },
  "resilience_before":{…},"resilience_after":{…} }
```

`POST /api/income/{id}/advance` (approved) → adds:
```json
{ "advance_id":"adv_…","virtual_account":"STHIR…@upi",
  "allocation":[ {"priority":0,"label":"Resilience savings","amount":2032},
                 {"priority":1,"label":"Rent","amount":9000},
                 {"priority":9,"label":"Spendable to worker","amount":0} ] }
```

---

## New: Crisis Override & Entitlement Bridge

### `GET /api/workers/{id}/entitlements?crisis_tag=&amount=`
Government schemes the worker is eligible for. With `crisis_tag` + `amount`, returns the **welfare offset** — how much free coverage replaces a loan.
```json
{ "state":"TN","schemes":[{"name":"TN Platform-Based Gig Workers Welfare Board","max_cover":1000000,"premium":"Free — state pays the premium"}],
  "best_cover":1000000,"welfare_offset":35000,"residual_loan_needed":0,"fully_covered":true,
  "message":"Rs 35,000 of this can be covered for free by TN … — no loan needed for that part." }
```

### `POST /api/workers/{id}/crisis-override`  (multipart: `file`, optional `requested`)
### `POST /api/workers/{id}/crisis-override/sample/{key}`  (`key` = `accident` | `hospital` | `breakdown`)
Classifies the bill/damage photo, runs the Entitlement Bridge **first**, then disburses any residual **direct to the vendor** over UPI. Three outcomes:
- `decision:"covered_by_entitlement"` — welfare covers it, no loan (accident/hospital demo).
- `decision:"emergency_advance_to_vendor"` — residual paid straight to the vendor, bypassing existing debt (breakdown demo).
- `decision:"not_a_crisis"` — the image isn't a genuine emergency.
```json
{ "assessment":{"crisis_type":"vehicle_breakdown","vendor_name":"Speed Auto Garage","estimated_amount":8500},
  "entitlement_bridge":{"schemes":[],"welfare_offset":0,"residual_loan_needed":8500},
  "decision":"emergency_advance_to_vendor",
  "emergency_advance":{"amount":8500,"paid_to":"Speed Auto Garage (direct UPI)",
    "note":"Paid straight to the vendor so no existing debt can intercept it."} }
```

---

## Bundled frontend (new)

Two runnable portals now ship **inside this repo** and are served by the same FastAPI
app (a single deploy serves the API *and* the UI):

| Path | Portal | Notes |
|---|---|---|
| `/` | Landing / portal chooser | `web/index.html` |
| `/worker.html` | **Worker portal** | Picker → dashboard → early warnings → submit → advance → savings → crisis override → free government cover. English · தமிழ் · हिन्दी. |
| `/lender.html` | **Lender console** | Portfolio · distress triage · income sources · verification & fraud · statutory exposure · live activity. Light & dark. |

Built with React 18 + HTM loaded from a CDN — **no build step**. Every screen fetches
from `/api/...` on the same origin, so nothing to configure. To point the UI at a
different API origin, set `window.STHIR_API` before the module script runs.

Run locally: `python -m app.seed && uvicorn app.main:app --reload` → open `http://localhost:8000/`.
