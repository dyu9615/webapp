"""
QuantAlpha News Station — Pydantic Validation Models
═════════════════════════════════════════════════════
Strict schemas that validate all AI output before it enters the system.
Prevents Bug 2 (Non-Deterministic AI Output):
  - All scores clamped to [-1.0, 1.0]
  - Missing fields trigger ValidationError → retry or fallback to rule-based
  - Enum labels prevent free-text hallucinations
"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from enum import Enum


class SentimentLabel(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class ImpactMatrixEntry(BaseModel):
    """Per-ticker sentiment impact extracted from a FactSet report."""
    ticker: str = Field(..., min_length=1, max_length=5)
    score: float = Field(..., ge=-1.0, le=1.0)
    label: SentimentLabel
    reasoning: str = Field(default="", max_length=200)

    @field_validator('ticker')
    @classmethod
    def uppercase_ticker(cls, v):
        return v.upper().strip()

    @field_validator('score')
    @classmethod
    def clamp_score(cls, v):
        return max(-1.0, min(1.0, round(v, 3)))


class MacroWarning(BaseModel):
    """Macro-level risk alert extracted from a report."""
    type: str = Field(..., max_length=50)        # "rate", "tariff", "policy", "geopolitical"
    severity: str = Field(..., max_length=20)     # "low", "medium", "high"
    description: str = Field(..., max_length=300)
    tickers_affected: List[str] = Field(default_factory=list)

    @field_validator('severity')
    @classmethod
    def validate_severity(cls, v):
        v = v.lower().strip()
        if v not in ('low', 'medium', 'high'):
            v = 'medium'
        return v


class SentimentResult(BaseModel):
    """Full AI analysis result for a FactSet PDF report.

    This is the contract between the AI agent and the rest of the system.
    If the AI returns anything that doesn't match this schema, pydantic
    raises ValidationError and the system falls back to rule-based analysis.
    """
    core_summary: str = Field(..., min_length=10, max_length=1000)
    impact_matrix: List[ImpactMatrixEntry] = Field(default_factory=list)
    macro_warnings: List[MacroWarning] = Field(default_factory=list)
    overall_score: float = Field(..., ge=-1.0, le=1.0)
    overall_label: SentimentLabel
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator('overall_score')
    @classmethod
    def clamp_overall(cls, v):
        return max(-1.0, min(1.0, round(v, 3)))

    @field_validator('confidence')
    @classmethod
    def clamp_confidence(cls, v):
        return max(0.0, min(1.0, round(v, 3)))


class BloombergArticle(BaseModel):
    """A Bloomberg news article from RapidAPI."""
    title: str
    url: str = ""
    source: str = "Bloomberg (RapidAPI)"
    published_at: str = ""
    summary: str = ""
    category: str = ""
    ai_status: str = "pending"    # "pending" | "scored" | "failed"
    sentiment_score: Optional[float] = None
    sentiment_label: Optional[SentimentLabel] = None
    cached_at: str = ""


class CongressBill(BaseModel):
    """A tracked bill from Congress.gov."""
    bill_id: str
    title: str
    url: str = ""
    introduced_date: str = ""
    sponsor: str = ""
    keywords_matched: List[str] = Field(default_factory=list)
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    status: str = ""
