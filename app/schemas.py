from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


RiskLevel = Literal["ok", "soft", "hard"]
ListingStatus = Literal["active", "hidden", "flagged"]


class ValidateListingRequest(BaseModel):
    carId: str | None = None
    make: str = Field(..., min_length=1, max_length=50)
    model: str = Field(..., min_length=1, max_length=50)
    variant: str = Field(..., min_length=1, max_length=50)
    location: str = Field(..., min_length=1)
    description: str | None = Field(default=None, max_length=1000)
    ownerHistory: str | None = Field(default=None, max_length=50)
    whenTOSell: str | None = Field(default=None, max_length=50)
    year: int = Field(..., ge=1900, le=2100)
    mileage: float = Field(..., ge=0)
    price: float | None = Field(default=None, ge=0)
    fuel_type: str | None = None
    transmission_type: str | None = None
    images: list[str] = Field(default_factory=list)


class ImageFinding(BaseModel):
    url: str
    labels: list[str] = Field(default_factory=list)
    score: float = 0.0


class ValidateListingResponse(BaseModel):
    riskLevel: RiskLevel
    status: ListingStatus
    flaggedForAdmin: bool
    categories: list[str] = Field(default_factory=list)
    score: float = Field(..., ge=0, le=1)
    feedback: str
    textFindings: list[str] = Field(default_factory=list)
    imageFindings: list[ImageFinding] = Field(default_factory=list)
    model: str
    reviewedAt: datetime


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
