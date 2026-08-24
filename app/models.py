from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, Field, HttpUrl
from typing import Any


class LeadStatus(str, Enum):
    NEW = 'new'
    ALERTED = 'alerted'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    SENT = 'sent'
    OPTED_OUT = 'opted_out'
    ERROR = 'error'


class LeadCreate(BaseModel):
    source: str
    source_url: str | None = None
    name: str
    business_type: str | None = None
    city: str | None = None
    website_url: str | None = None
    email: str | None = None
    phone: str | None = None
    social_url: str | None = None
    opportunity_type: str = 'website_lead'
    raw_text: str | None = None
    fingerprint: str


class LeadOut(BaseModel):
    id: int
    source: str
    source_url: str | None = None
    name: str
    business_type: str | None = None
    city: str | None = None
    website_url: str | None = None
    email: str | None = None
    phone: str | None = None
    social_url: str | None = None
    status: LeadStatus
    need_score: int = 0
    opportunity_type: str = 'website_lead'
    ai_summary: str | None = None
    ai_reason: str | None = None
    created_at: str
    updated_at: str


class ClassificationResult(BaseModel):
    score: int = Field(ge=0, le=100)
    category: str = 'unknown'
    summary: str = ''
    reason: str = ''
    risk_flags: list[str] = Field(default_factory=list)
    recommended_action: str = 'review'
    draft_message: str = ''


class SourceRunResult(BaseModel):
    source: str
    found: int = 0
    inserted: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)


class AppHealth(BaseModel):
    status: str
    db: str
    telegram_configured: bool
    smtp_configured: bool
    groq_configured: bool
    search_configured: bool
