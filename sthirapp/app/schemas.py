"""The strict contract between the extraction brain and everything downstream.

This schema is frozen first and changed last. Every service below the
extractor is written against it, so a change here costs the whole team.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    description: str
    hsn_code: str | None = None
    quantity: float | None = None
    rate: float | None = None
    amount: float | None = None


class FieldConfidence(BaseModel):
    buyer_name: float = 0.0
    buyer_gstin: float = 0.0
    invoice_number: float = 0.0
    total_amount: float = 0.0
    dates: float = 0.0


class InvoiceExtraction(BaseModel):
    """What the vision model must return. No free-form prose anywhere."""
    # True only when the image is a genuine proof of income (invoice, payout
    # screenshot, receipt, bill or payslip). False for selfies, memes, random
    # photos - the pipeline rejects those instead of booking them as income.
    is_income_document: bool = True
    document_type: Literal["tax_invoice", "e_invoice", "delivery_note",
                           "payout_screenshot", "receipt", "payslip",
                           "not_income", "unknown"]
    is_handwritten: bool
    languages: list[str] = Field(default_factory=list)

    seller_name: str | None = None
    seller_gstin: str | None = None
    buyer_name: str | None = None
    buyer_gstin: str | None = None

    invoice_number: str | None = None
    invoice_date: str | None = None            # dd/mm/yyyy as printed
    due_date: str | None = None
    payment_terms_days: int | None = None

    total_amount: float | None = None
    currency: str = "INR"
    line_items: list[LineItem] = Field(default_factory=list)

    irn_printed: str | None = None
    vernacular_notes: list[str] = Field(default_factory=list)
    field_confidence: FieldConfidence = Field(default_factory=FieldConfidence)
    overall_confidence: float = 0.0


class SubmitRequest(BaseModel):
    mill_id: str
    source: Literal["whatsapp", "console", "voice"] = "whatsapp"
    filename: str | None = None


class ClarifyAnswer(BaseModel):
    invoice_id: str
    field: str
    value: str


class CrisisAssessment(BaseModel):
    """What the vision model returns for an emergency bill / damage photo."""
    is_genuine_crisis: bool
    crisis_type: Literal["vehicle_accident", "medical", "hospitalization",
                         "injury", "vehicle_breakdown", "property_damage",
                         "other", "unclear"]
    vendor_name: str | None = None
    estimated_amount: float | None = None
    urgency: Literal["immediate", "high", "moderate", "low"] = "high"
    evidence_summary: str = ""
    confidence: float = 0.0
