import json
import logging
import re
from datetime import datetime, timezone

from openai import OpenAI

from app.config import Settings, get_settings
from app.schemas import (
    ImageFinding,
    ListingStatus,
    RiskLevel,
    ValidateListingRequest,
    ValidateListingResponse,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You moderate used-car marketplace listings. Respond with JSON only."
)

USER_PROMPT_TEMPLATE = """You are a marketplace listing moderator for used cars in India.
Analyze the listing text and images. Return ONLY valid JSON with this shape:
{{
  "riskLevel": "ok" | "soft" | "hard",
  "categories": string[],
  "score": number,
  "feedback": string,
  "textFindings": string[],
  "imageFindings": [{{ "url": string, "labels": string[], "score": number }}]
}}

Rules:
- hard: NSFW, clear scam, phishing, fake documents, non-car pornographic content
- soft: contact info in description, spammy wording, suspicious price, weak/irrelevant images
- ok: normal used-car listing

Listing:
{listing_text}
"""


def map_to_status(
    risk_level: RiskLevel, categories: list[str]
) -> tuple[ListingStatus, bool]:
    if risk_level == "ok":
        return "active", False
    if risk_level == "soft":
        return "active", True
    if "nsfw" in categories:
        return "hidden", True
    return "flagged", True


class ModeratorService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def validate(
        self, payload: ValidateListingRequest
    ) -> ValidateListingResponse:
        if self.settings.openai_api_key:
            try:
                return self._validate_with_openai(payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "OpenAI moderation failed, using rules fallback: %s", exc
                )
        return self._validate_with_rules(payload)

    def _listing_text(self, payload: ValidateListingRequest) -> str:
        parts = [
            payload.make,
            payload.model,
            payload.variant,
            payload.location,
            payload.description or "",
            payload.ownerHistory or "",
            payload.whenTOSell or "",
            f"year:{payload.year}",
            f"mileage:{payload.mileage}",
        ]
        if payload.price is not None:
            parts.append(f"price:{payload.price}")
        if payload.fuel_type:
            parts.append(f"fuel:{payload.fuel_type}")
        if payload.transmission_type:
            parts.append(f"transmission:{payload.transmission_type}")
        return "\n".join(p for p in parts if p)

    def _validate_with_openai(
        self, payload: ValidateListingRequest
    ) -> ValidateListingResponse:
        client = OpenAI(api_key=self.settings.openai_api_key)
        image_urls = (payload.images or [])[:5]

        content: list[dict] = [
            {
                "type": "text",
                "text": USER_PROMPT_TEMPLATE.format(
                    listing_text=self._listing_text(payload)
                ),
            }
        ]
        for url in image_urls:
            content.append(
                {"type": "image_url", "image_url": {"url": url}}
            )

        response = client.chat.completions.create(
            model=self.settings.openai_model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
        )

        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)

        risk_level: RiskLevel = parsed.get("riskLevel") or "ok"
        if risk_level not in ("ok", "soft", "hard"):
            risk_level = "ok"

        categories = list(parsed.get("categories") or [])
        status, flagged_for_admin = map_to_status(risk_level, categories)
        score = parsed.get("score")
        if not isinstance(score, (int, float)):
            score = 0.0
        score = max(0.0, min(1.0, float(score)))

        image_findings = [
            ImageFinding(
                url=item.get("url", ""),
                labels=list(item.get("labels") or []),
                score=float(item.get("score") or 0),
            )
            for item in (parsed.get("imageFindings") or [])
            if isinstance(item, dict)
        ]

        return ValidateListingResponse(
            riskLevel=risk_level,
            status=status,
            flaggedForAdmin=flagged_for_admin,
            categories=categories,
            score=score,
            feedback=parsed.get("feedback") or "Validation complete.",
            textFindings=list(parsed.get("textFindings") or []),
            imageFindings=image_findings,
            model=self.settings.openai_model,
            reviewedAt=datetime.now(timezone.utc),
        )

    def _validate_with_rules(
        self, payload: ValidateListingRequest
    ) -> ValidateListingResponse:
        categories: set[str] = set()
        text_findings: list[str] = []
        image_findings: list[ImageFinding] = []

        text = " ".join(
            filter(
                None,
                [
                    payload.description,
                    payload.ownerHistory,
                    payload.whenTOSell,
                    payload.location,
                ],
            )
        ).lower()

        if re.search(r"(\+?\d[\d\s\-()]{8,}\d)", text) or re.search(
            r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", text, re.I
        ) or re.search(r"whatsapp|wa\.me|telegram|call me|dm me", text, re.I):
            categories.add("contact_leak")
            text_findings.append("Contact details found in listing text.")

        if re.search(
            r"guaranteed profit|double your money|loan approved|no inspection|"
            r"urgent sale only|wire transfer|crypto only|gift card",
            text,
            re.I,
        ):
            categories.add("scam")
            text_findings.append("Scam-like language detected.")

        bangs = (payload.description or "").count("!")
        if bangs > 5 or re.search(
            r"best deal!!!!|click here|limited offer|act now|"
            r"100% genuine certificate free",
            text,
            re.I,
        ):
            categories.add("spam")
            text_findings.append("Spammy wording detected.")

        if re.search(r"\b(nude|porn|xxx|sex|nsfw|onlyfans)\b", text, re.I):
            categories.add("nsfw")
            text_findings.append("NSFW content detected in text.")

        images = payload.images or []
        if not images:
            categories.add("missing_images")
            text_findings.append("No images provided.")
        else:
            for url in images:
                image_findings.append(
                    ImageFinding(
                        url=url,
                        labels=["unchecked_without_vision_api"],
                        score=0.0,
                    )
                )

        current_year = datetime.now(timezone.utc).year
        if (
            payload.price is not None
            and 0 < payload.price < 10_000
            and payload.year >= current_year - 5
        ):
            categories.add("price_anomaly")
            text_findings.append("Suspiciously low price for a recent car.")

        if "nsfw" in categories or "scam" in categories:
            risk_level: RiskLevel = "hard"
        elif categories:
            risk_level = "soft"
        else:
            risk_level = "ok"

        status, flagged_for_admin = map_to_status(risk_level, list(categories))

        if risk_level == "ok":
            feedback = "Listing looks fine."
            score = 0.0
        elif risk_level == "soft":
            feedback = "Listing kept live but flagged for admin review."
            score = 0.55
        elif status == "hidden":
            feedback = "Listing auto-hidden due to hard policy risk (NSFW)."
            score = 0.9
        else:
            feedback = "Listing flagged and hidden due to hard policy risk."
            score = 0.9

        return ValidateListingResponse(
            riskLevel=risk_level,
            status=status,
            flaggedForAdmin=flagged_for_admin,
            categories=sorted(categories),
            score=score,
            feedback=feedback,
            textFindings=text_findings,
            imageFindings=image_findings,
            model="rules-fallback",
            reviewedAt=datetime.now(timezone.utc),
        )
