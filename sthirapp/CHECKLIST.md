# Sthir — Functionality Checklist

> **QA run: 2026-09-03 — ALL FUNCTIONAL ITEMS PASS ✅**
> Verified end-to-end against a live server (seed → API + both portals driven in a real browser).
> Backend flows checked via the API; every screen and interaction driven in-page.
> All money is simulated. Demo date is fixed at **2026-09-03**.

| Area | Result |
|---|---|
| §0 Setup & serving | ✅ pass |
| §1 Worker login | ✅ pass |
| §2 Worker dashboard (incl. calendar + reminders) | ✅ pass |
| §3 Worker core flows (incl. photo upload) | ✅ pass |
| §4 Lender login | ✅ pass |
| §5 Lender screens | ✅ pass |
| §6 Cross-cutting | ✅ pass |

## 0. Setup
- [x] `cd ~/sthir && ./.venv/bin/python -m app.seed` seeds 4 workers, 7 sources, 65 income events
- [x] `./.venv/bin/python -m uvicorn app.main:app --port 8000` starts with no errors
- [x] http://localhost:8000/ shows the landing page with both portal cards
- [x] http://localhost:8000/docs shows the interactive API (**22 endpoints** confirmed)
- [x] **After any backend (.py) change, restart the server** — Python is not hot-reloaded unless you pass `--reload`

## 1. Worker portal — login  (`/worker.html`)
- [x] Shows the sign-in screen (phone + password), not a worker list
- [x] "Demo logins · tap to fill" lists all 4 workers with phone numbers
- [x] Tapping a demo account fills phone + password
- [x] Correct login (any demo phone + `sthir2026`) opens that worker's dashboard
- [x] Wrong password → "Incorrect password"; unknown phone → "No account found"
- [x] Reload keeps you signed in (remembered by phone — verified with a hard reload)
- [x] Sidebar "Sign out" returns to the login screen (clears the saved phone)
- [x] Language toggle (English / தமிழ் / हिन्दी) changes the UI chrome

## 2. Worker portal — dashboard
- [x] Navy greeting hero shows the right name + today's alert count
- [x] Resilience gauge, band pill, and runway (days) match the worker
- [x] 12-week income bars render; sub-scores (runway/savings/stability/coverage) show
- [x] Monthly obligations bar splits essential (green) vs debt service (red)
- [x] **Calendar** shows the demo month with today (3rd) highlighted, weekends in red, dots on bill-due days
- [x] **Reminders** lists the worker's real recurring bills (rent, EMIs, utility, family), sorted by due date, with a red APR flag on predatory debt (Ravi's 60% moneylender)

## 3. Worker portal — core flows
- [x] **Early warnings**: real alerts with graded intervention ladders
- [x] **Income history**: weekly groups, tier badges (A/B/C)
- [x] **Submit income → pick a demo item** → verify → tier result → resilience before/after
- [x] **Submit income → Upload a new photo**: click the dashed box opens the file picker
  - [x] After choosing a file, the amount / type / reference form appears
  - [x] "Verify & submit" uploads it, shows the tier result, and refreshes resilience
  - [x] A blank amount is rejected ("Enter the amount"); a corrupt/tiny file still processes (no 500 — backend hardened)
- [x] **Request advance (Lakshmi)** → approved → allocation waterfall + virtual UPI account
- [x] **Request advance (Ravi)** → **refused** (protective) → shows the refinance alternative
- [x] **Savings**: balance + sweep/drawdown ledger
- [x] **Free government cover**: lists eligible schemes (Max cover shown)
- [x] **I have an emergency (Crisis)**:
  - [x] Accident / Hospital → "fully covered for free … No loan needed" (₹0 borrowed)
  - [x] Vehicle breakdown → "Paid ₹X directly to Speed Auto Garage over UPI"

## 4. Lender console — login  (`/lender.html`)
- [x] Shows "Lender console sign-in" (work email + password), console hidden until authed
- [x] `admin@sthir.in` (or `admin`) + `sthir2026` signs in
- [x] Wrong password → "Incorrect password"
- [x] Reload keeps the session; "Sign out" returns to login and clears it
- [x] Dark/Light theme toggle works on the login screen and inside the console

## 5. Lender console — screens
- [x] **Portfolio**: KPI cards (workers, distress, outstanding, protective refusals, fraud rings) + band distribution
- [x] **Distress caseload**: triage table; selecting a case loads its intervention ladder
- [x] **Income sources**: reliability grades; "Flag" adds a flag (2 flags = amber)
- [x] **Verification & fraud**: live tier mix (A/B/C) across all income; fraud-ring state
- [x] **Statutory exposure**: pick an invoice → s.16 interest + s.43B(h) + total cost of waiting
- [x] **Activity**: live event stream, polling every 4s (submit/advance actions appear here)
- [x] **Worker detail**: read-only borrower view opens from a distress case

## 6. Cross-cutting
- [x] Header ↺ / "Reset demo data" reseeds to a clean state in <2s (verified via `/api/demo/reset`)
- [x] Every page keeps a visible "Demo · simulated funds / PROTOTYPE" label
- [x] No console errors on load (checked — zero script errors; only benign `ERR_CONNECTION_REFUSED` if the server is down)
- [x] Deploy config present & valid: `Procfile`, `railway.json`, `requirements.txt` in `~/sthir`.
      *Running `railway up` and setting `GEMINI_API_KEY` is a manual step you perform when you're ready — the offline fallback works without the key.*

## Demo credentials
| Portal | Login | Password |
|---|---|---|
| Worker | any demo phone (e.g. `+919600012345` = Ravi) | `sthir2026` |
| Lender | `admin@sthir.in` | `sthir2026` |
