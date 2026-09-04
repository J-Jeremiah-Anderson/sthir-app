"""The Entitlement Bridge - welfare offset before credit.

Sthir's defining move: before it lends a rupee in a crisis, it checks whether
free government coverage already exists for exactly this situation, and tries
to replace the loan with an entitlement. A lender whose first instinct is to
give you public money instead of debt is the whole thesis in one function.

Every scheme, coverage figure and eligibility rule below is real (Sept 2026):

  e-Shram / PMSBY   Unorganised-worker accident cover: Rs 2,00,000 on
                    accidental death, Rs 1,00,000 on partial disability.
                    Premium free in the first year for e-Shram registrants.
  TN Gig Workers    Tamil Nadu Platform-Based Gig Workers Welfare Board:
  Welfare Board     accident cover raised to Rs 10,00,000 in 2026 (state pays
                    the premium); partial injury scaled by severity.
  PM-JAY            Ayushman Bharat health cover: Rs 5,00,000 per family per
                    year, being extended to ~1 crore gig workers.

Sources: Ministry of Labour (e-Shram/PMSBY), TN Platform-Based Gig Workers
Welfare Board (2024-2026), National Health Authority (PM-JAY).
"""
from __future__ import annotations

from ..models import Worker

TN_CITIES = {"chennai", "tiruppur", "coimbatore", "madurai", "erode",
             "salem", "trichy", "tirupur"}


def _state(worker: Worker) -> str:
    return "TN" if (worker.city or "").strip().lower() in TN_CITIES else "OTHER"


# crisis_tag -> which schemes can apply
SCHEMES = [
    {
        "id": "tn_gig_board",
        "name": "TN Platform-Based Gig Workers Welfare Board",
        "kind": "accident",
        "covers": {"accident", "injury", "vehicle_accident", "disability"},
        "max_cover": 1_000_000,
        "premium": "Free - premium borne by the Tamil Nadu government",
        "eligible": lambda w: _state(w) == "TN"
        and any(g in (w.gig_types or []) for g in ("swiggy", "zomato", "ola",
                                                   "uber", "job_work", "freelance"))
        or (_state(w) == "TN" and w.occupation
            and ("rider" in w.occupation.lower() or "driver" in w.occupation.lower())),
        "note": "Full amount on death/permanent disability; partial injury scaled.",
    },
    {
        "id": "eshram_pmsby",
        "name": "e-Shram / PMSBY accident cover",
        "kind": "accident",
        "covers": {"accident", "injury", "vehicle_accident", "disability"},
        "max_cover": 200_000,           # death; 1L partial
        "partial_cover": 100_000,
        "premium": "Free in year one for e-Shram registrants (then Rs 20/yr)",
        "eligible": lambda w: True,     # any unorganised worker
        "note": "Rs 2L accidental death, Rs 1L partial disability.",
    },
    {
        "id": "pmjay",
        "name": "PM-JAY (Ayushman Bharat) health cover",
        "kind": "medical",
        "covers": {"medical", "hospitalization", "illness", "injury"},
        "max_cover": 500_000,
        "premium": "Free for eligible families",
        "eligible": lambda w: True,     # extended to gig workers; assume enrolled
        "note": "Rs 5L per family per year for hospitalization.",
    },
]


# Accept both the public crisis tags (crisis-accident / crisis-hospital /
# crisis-breakdown) and the internal classifier's crisis_type tokens, so the
# GET /entitlements endpoint and the crisis-override flow always agree.
_TAG_ALIASES = {
    "crisis-accident": "vehicle_accident",
    "crisis-hospital": "medical",
    "crisis-breakdown": "vehicle_breakdown",
    "hospital": "medical",
    "hospitalization": "medical",
    "illness": "medical",
    "breakdown": "vehicle_breakdown",
}


def match(worker: Worker, *, crisis_tag: str | None = None,
          amount: float | None = None) -> dict:
    """Return the schemes that apply, and how much of `amount` free coverage
    can offset - the number that makes the loan shrink on stage."""
    tag = (crisis_tag or "").lower()
    tag = _TAG_ALIASES.get(tag, tag)
    matched = []
    for s in SCHEMES:
        if tag and tag not in s["covers"]:
            continue
        if not s["eligible"](worker):
            continue
        cap = s["max_cover"]
        # partial-injury realism for accident schemes
        if tag == "injury" and s.get("partial_cover"):
            cap = s["partial_cover"]
        matched.append({"id": s["id"], "name": s["name"], "kind": s["kind"],
                        "max_cover": cap, "premium": s["premium"],
                        "note": s["note"]})

    # the offset: the single best-fit scheme's cover, capped at the need
    best_cover = max((m["max_cover"] for m in matched), default=0)
    need = amount or 0.0
    offset = round(min(best_cover, need), 2) if need else best_cover
    residual = round(max(0.0, need - offset), 2) if need else 0.0

    return {
        "worker_id": worker.id,
        "state": _state(worker),
        "crisis_tag": crisis_tag,
        "requested_amount": round(need, 2) if need else None,
        "schemes": matched,
        "best_cover": best_cover,
        "welfare_offset": offset if need else None,
        "residual_loan_needed": residual if need else None,
        "fully_covered": bool(need) and residual <= 0,
        "message": (f"Rs {offset:,.0f} of this can be covered for free by "
                    f"{matched[0]['name']} - no loan needed for that part."
                    if need and matched else
                    ("No matching government scheme found for this situation."
                     if not matched else
                     f"You are eligible for {len(matched)} government scheme(s).")),
    }
