"""Runtime configuration. Everything has a demo-safe default."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("FF_DATA_DIR", ROOT / "data"))
INVOICE_DIR = DATA_DIR / "invoices"

# SQLite by default so the whole thing runs with zero infrastructure.
# docker-compose sets DATABASE_URL to Postgres.
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'fabricfund.db'}")

# Extraction provider. "auto" picks Gemini when a Google key is present,
# then Claude, then the deterministic offline extractor.
EXTRACTOR = os.getenv("FF_EXTRACTOR", "auto")   # auto | gemini | claude | offline

# Model IDs move fast. GEMINI_MODEL may be left unset, in which case the
# provider is asked which models it actually serves and the best match from
# PREFERRED_GEMINI is used - a hardcoded ID that has since been retired is a
# bad way to lose a demo.
# Default to a fast flash-lite model: Pro models are blocked on the free tier
# (quota 0) and full-flash models time out, but flash-lite serves reliably.
# An explicit FF_GEMINI_MODEL always overrides this.
GEMINI_MODEL = os.getenv("FF_GEMINI_MODEL", "gemini-flash-lite-latest")
PREFERRED_GEMINI = ("gemini-flash-lite-latest", "gemini-3.1-flash-lite",
                    "gemini-3.5-flash-lite", "gemini-3.6-flash",
                    "gemini-flash-latest")
CLAUDE_MODEL = os.getenv("FF_CLAUDE_MODEL", "claude-opus-5")

# The mock NIC signing key. A real e-invoice QR is signed by the Invoice
# Registration Portal; we sign our synthetic ones with this so signature
# verification in the demo is real cryptography against a stand-in key.
MOCK_IRP_SECRET = os.getenv("FF_IRP_SECRET", "fabricfund-mock-irp-key")

# --- Credit policy knobs (see app/services/decide.py) ---
BASE_ADVANCE_RATE = 0.80
ADVANCE_FLOOR = 0.60
ADVANCE_CAP = 0.90
CONFIDENCE_GATE = 0.85
COST_OF_FUNDS_30D = 0.011
MAX_EXPOSURE_PER_BUYER = 0.25
MAX_EXPOSURE_PER_MILL = 0.15
BOOK_SIZE = 5_000_000.0   # notional lender book for exposure limits

DATA_DIR.mkdir(parents=True, exist_ok=True)
INVOICE_DIR.mkdir(parents=True, exist_ok=True)
