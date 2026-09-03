"""Sthir data model - income resilience for gig & informal workers.

Nine tables. The design fuses two problem statements:
  - Financial resilience for irregular income (savings, responsible credit)
  - Early detection of financial distress, before it becomes a crisis

Generalised from the invoice-lending original: a `Buyer` became an
`IncomeSource` (a gig platform, a client, or a buyer), an `Invoice` became an
`IncomeEvent` (a payout, an invoice, a cash gig), and the mill's burn lines
became first-class `Obligation` rows so we can reason about affordability.

The `events` table stays append-only so the whole demo replays from clean.
"""
import datetime as dt
import uuid

from sqlalchemy import (Column, String, Float, Integer, Boolean, DateTime,
                        ForeignKey, Text, JSON, Index)
from sqlalchemy.orm import relationship

from .db import Base


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Worker(Base):
    """A gig or informal worker with irregular income. No signup flow in the
    demo - personas are seeded."""
    __tablename__ = "workers"

    id = Column(String, primary_key=True, default=lambda: _id("wrk"))
    name = Column(String, nullable=False)
    phone = Column(String, unique=True)          # WhatsApp identity
    city = Column(String, default="Chennai")
    occupation = Column(String)                  # "Delivery rider", "Tailor", ...
    gig_types = Column(JSON, default=list)       # ["swiggy","zomato"] etc.
    languages = Column(JSON, default=list)       # ["ta","en"]
    digital_literacy = Column(String, default="medium")  # low|medium|high

    cash_buffer = Column(Float, default=0.0)     # spendable cash on hand
    savings_balance = Column(Float, default=0.0) # locked resilience buffer
    autosave_rate = Column(Float, default=0.10)  # fraction of surplus swept
    consent_credit = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_now)

    income_events = relationship("IncomeEvent", back_populates="worker")
    obligations = relationship("Obligation", back_populates="worker")
    alerts = relationship("Alert", back_populates="worker")


class IncomeSource(Base):
    """Where money comes from. Reliability here predicts whether irregular
    income will actually land - the core of the resilience model."""
    __tablename__ = "income_sources"

    id = Column(String, primary_key=True, default=lambda: _id("src"))
    name = Column(String, nullable=False)
    kind = Column(String, default="gig_platform")  # gig_platform|client|buyer|employer
    gstin = Column(String)                          # only for invoice-type payers
    turnover_band = Column(String)
    payments_observed = Column(Integer, default=0)
    payments_on_time = Column(Integer, default=0)
    avg_days_late = Column(Float, default=0.0)
    flagged_by_workers = Column(JSON, default=list)
    created_at = Column(DateTime, default=_now)

    events = relationship("IncomeEvent", back_populates="source")

    @property
    def reliability(self) -> float:
        if self.payments_observed == 0:
            return 0.5
        return self.payments_on_time / self.payments_observed


class IncomeEvent(Base):
    """One unit of income: a weekly gig payout, an invoice, or a cash gig.

    Carries the same verification ladder as the original invoice - tier A is a
    cryptographically or platform-verified payout, C is a photo an AI read.
    """
    __tablename__ = "income_events"

    id = Column(String, primary_key=True, default=lambda: _id("inc"))
    worker_id = Column(String, ForeignKey("workers.id"), nullable=False)
    source_id = Column(String, ForeignKey("income_sources.id"))

    kind = Column(String, default="gig_payout")   # gig_payout|invoice|cash_gig|salary
    reference = Column(String)                     # payout id / invoice no
    gross = Column(Float, nullable=False)
    expected_date = Column(DateTime)               # when it should arrive
    received_date = Column(DateTime)               # when it actually did (null = pending)

    tier = Column(String, default="C")             # A|B|C verification
    verified = Column(Boolean, default=False)
    verification_json = Column(JSON)
    confidence = Column(Float, default=0.0)
    irn = Column(String, index=True)
    fuzzy_key = Column(String, index=True)

    evidence_path = Column(String)                 # screenshot / invoice image
    evidence_phash = Column(String, index=True)

    status = Column(String, default="expected")    # expected|received|advanced|flagged
    created_at = Column(DateTime, default=_now)

    worker = relationship("Worker", back_populates="income_events")
    source = relationship("IncomeSource", back_populates="events")
    advance = relationship("Advance", back_populates="income_event", uselist=False)


Index("ix_income_dedupe", IncomeEvent.fuzzy_key, IncomeEvent.status)


class Obligation(Base):
    """A recurring outflow. First-class rows so affordability and the
    allocation waterfall can reason per-obligation."""
    __tablename__ = "obligations"

    id = Column(String, primary_key=True, default=lambda: _id("obl"))
    worker_id = Column(String, ForeignKey("workers.id"), nullable=False)
    head = Column(String, nullable=False)          # "Room rent", "Bike EMI", ...
    payee = Column(String)
    category = Column(String, default="other")     # essential|emi|rent|utility|family|input|savings
    monthly = Column(Float, default=0.0)
    due_day = Column(Integer, default=1)
    priority = Column(Integer, default=5)
    is_debt = Column(Boolean, default=False)       # marks existing borrowing
    apr = Column(Float, default=0.0)               # for existing debt (moneylender!)
    created_at = Column(DateTime, default=_now)

    worker = relationship("Worker", back_populates="obligations")


class Advance(Base):
    """A responsible income-smoothing advance against verified upcoming income.

    Distinct from a payday loan by construction: it is gated on affordability
    and refused when it would worsen distress. `reasons` is stored so every
    decision - including a refusal - is explainable."""
    __tablename__ = "advances"

    id = Column(String, primary_key=True, default=lambda: _id("adv"))
    income_event_id = Column(String, ForeignKey("income_events.id"))
    worker_id = Column(String, ForeignKey("workers.id"), nullable=False)

    advance_rate = Column(Float, default=0.0)
    face_value = Column(Float, default=0.0)
    advance_amount = Column(Float, default=0.0)
    flat_fee = Column(Float, default=0.0)
    apr_equiv = Column(Float, default=0.0)
    net_disbursed = Column(Float, default=0.0)
    reasons = Column(JSON, default=list)
    decision = Column(String, default="approved")   # approved|refused|clarify
    refuse_reason = Column(Text)
    virtual_account = Column(String)
    status = Column(String, default="disbursed")    # disbursed|repaid
    created_at = Column(DateTime, default=_now)

    income_event = relationship("IncomeEvent", back_populates="advance")
    allocations = relationship("Allocation", back_populates="advance",
                               order_by="Allocation.priority")


class Allocation(Base):
    """One rung of the smart-allocation waterfall: obligations first, then a
    savings sweep, then spendable to the worker."""
    __tablename__ = "allocations"

    id = Column(String, primary_key=True, default=lambda: _id("alc"))
    advance_id = Column(String, ForeignKey("advances.id"))
    worker_id = Column(String, ForeignKey("workers.id"), nullable=False)
    priority = Column(Integer, nullable=False)
    payee = Column(String, nullable=False)
    category = Column(String)
    amount = Column(Float, nullable=False)
    status = Column(String, default="settled")
    created_at = Column(DateTime, default=_now)

    advance = relationship("Advance", back_populates="allocations")


class SavingsTxn(Base):
    """Auto-savings ledger: sweeps in on good weeks, draws down on lean ones."""
    __tablename__ = "savings_txns"

    id = Column(String, primary_key=True, default=lambda: _id("sav"))
    worker_id = Column(String, ForeignKey("workers.id"), nullable=False)
    direction = Column(String)                      # sweep_in|draw_down
    amount = Column(Float, nullable=False)
    reason = Column(String)
    balance_after = Column(Float)
    ts = Column(DateTime, default=_now)


class Alert(Base):
    """A distress signal and the intervention offered for it (statement 3)."""
    __tablename__ = "alerts"

    id = Column(String, primary_key=True, default=lambda: _id("alrt"))
    worker_id = Column(String, ForeignKey("workers.id"), nullable=False)
    severity = Column(String, default="info")       # info|watch|warning|critical
    code = Column(String)                           # runway_low|income_drop|payout_overdue|debt_spiral
    title = Column(String)
    detail = Column(Text)
    signals = Column(JSON, default=list)
    intervention = Column(JSON)                     # the recommended next step
    status = Column(String, default="open")         # open|acted|dismissed
    created_at = Column(DateTime, default=_now)

    worker = relationship("Worker", back_populates="alerts")


class Event(Base):
    """Append-only audit + replay log."""
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime, default=_now, index=True)
    type = Column(String, nullable=False, index=True)
    subject_id = Column(String, index=True)
    payload = Column(JSON, default=dict)
