import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.schemas import ParsedCar, ParseCarsRequest, ParseCarsResponse

logger = logging.getLogger(__name__)

FUEL_TYPES = {"Petrol", "Diesel", "Electric", "Hybrid", "CNG"}
TRANSMISSION_TYPES = {"Manual", "Automatic", "CVT", "AMT", "DCT"}

FUEL_ALIASES = {
    "petrol": "Petrol",
    "gasoline": "Petrol",
    "diesel": "Diesel",
    "electric": "Electric",
    "ev": "Electric",
    "hybrid": "Hybrid",
    "cng": "CNG",
}

TRANSMISSION_ALIASES = {
    "manual": "Manual",
    "mt": "Manual",
    "automatic": "Automatic",
    "auto": "Automatic",
    "at": "Automatic",
    "cvt": "CVT",
    "amt": "AMT",
    "dct": "DCT",
    "dsg": "DCT",
}

SYSTEM_PROMPT = """You extract used-car inventory from WhatsApp-style dealer messages in India.
Messages often use PreOwn marketing cards with emojis and labeled fields, for example:

🚗 Hyundai Grand i10 Sportz Automatic | 2013 Model | Petrol
✨ A stylish hatchback...
🚘 Car Details:
Brand: Hyundai
Model: Grand i10 Sportz Automatic
Manufacturing Year: 2013
Ownership: 2nd Owner
Fuel Type: Petrol
Transmission: Automatic
Kilometres Driven: 71,000 KM
Insurance: Valid
Features: Touchscreen Music System | Reverse Camera
Condition: Excellent
Highlights: Neat & Clean Interior | Ready to Drive
💰 Asking Price: ₹2,45,000
• Price Slightly Negotiable.
• Loan Facility Available.
📞 Interested buyers...
🚘 PreOwn – Buy Better. Sell Smarter.

Return ONLY valid JSON (no markdown) with this exact shape:
{
  "cars": [
    {
      "make": string,
      "model": string,
      "variant": string,
      "location": string,
      "year": number,
      "mileage": number,
      "price": number | null,
      "fuel_type": "Petrol" | "Diesel" | "Electric" | "Hybrid" | "CNG",
      "transmission_type": "Manual" | "Automatic" | "CVT" | "AMT" | "DCT",
      "ownerHistory": string | null,
      "whenTOSell": string | null,
      "marketingBlurb": string | null,
      "features": string | null,
      "condition": string | null,
      "highlights": string | null,
      "insurance": string | null,
      "sellingPoints": string[] | null
    }
  ],
  "warnings": string[]
}

Field mapping for PreOwn-style cards:
- Brand → make
- Model line may include variant + transmission (e.g. "Grand i10 Sportz Automatic") → split into model + variant; put transmission only in transmission_type, not in model/variant names when possible
- Manufacturing Year / "YYYY Model" → year
- Ownership / "1st Owner" / "2nd Owner" → ownerHistory
- Fuel Type → fuel_type
- Transmission → transmission_type
- Kilometres Driven / "71,000 KM" / "71k" → mileage (integer km)
- Asking Price / "₹2,45,000" / "2.45 Lakh" → price in whole INR
- ✨ paragraph → marketingBlurb (short, specific to this make/model/variant; do not invent fake features)
- Features / Condition / Highlights / Insurance lines → those fields when present
- Bullet-style offer lines (Price Negotiable, Loan Facility Available, Exchange Available, etc.) → sellingPoints[]
- Do NOT return a final description field — the server builds the PreOwn card text from these fields.
- Location: use city/area if mentioned; else "Unknown" + warning

Rules:
- Extract ALL cars mentioned (1–50). Never invent cars not in the message.
- dealerId is context only — do not invent cars from it.
- Normalize Indian slang carefully for mileage and price.
- fuel_type and transmission_type must be exact enum values above.
- If variant missing, use "Base" and add a warning.
- If location missing, use "Unknown" and add a warning.
- marketingBlurb must mention this car's make/model/variant naturally when writing copy.
- Omit images. Do not return status or owner.
- Optional fields: omit or null when unknown.
- Put human-readable notes about guesses/missing fields in warnings.
"""

USER_PROMPT_TEMPLATE = """dealerId (context only): {dealer_id}

WhatsApp inventory message:
---
{message}
---
"""


def strip_markdown_fences(raw: str) -> str:
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, re.I)
    if fence:
        return fence.group(1).strip()
    return text


def normalize_mileage(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None

    text = str(value).strip().lower().replace(",", "").replace(" ", "")
    text = re.sub(r"(km|kms|kilometres|kilometers)$", "", text)
    match = re.match(r"^(\d+(?:\.\d+)?)\s*k$", text)
    if match:
        return float(match.group(1)) * 1000
    match = re.match(r"^(\d+(?:\.\d+)?)$", text)
    if match:
        return float(match.group(1))
    return None


def normalize_price(value: Any) -> float | None:
    """Normalize price to whole INR. Numeric values under 1000 are treated as lakhs
    (e.g. 14.5 → 1450000) because models often return lakh figures without a unit.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        if value < 0:
            return None
        if 0 < float(value) < 1000:
            return float(int(round(float(value) * 100_000)))
        return float(int(round(float(value))))

    text = str(value).strip()
    text = text.replace("₹", "").replace("rs.", "").replace("rs", "").replace(",", "")
    text = re.sub(r"\s+", " ", text, flags=re.I).strip().lower()

    lakh = re.match(r"^(\d+(?:\.\d+)?)\s*(lakh|lac|lacs|lakhs|l)$", text)
    if lakh:
        return float(int(round(float(lakh.group(1)) * 100_000)))

    crore = re.match(r"^(\d+(?:\.\d+)?)\s*(crore|cr)$", text)
    if crore:
        return float(int(round(float(crore.group(1)) * 10_000_000)))

    digits = re.match(r"^(\d+(?:\.\d+)?)$", text)
    if digits:
        amount = float(digits.group(1))
        if 0 < amount < 1000:
            return float(int(round(amount * 100_000)))
        return float(int(round(amount)))
    return None


def normalize_fuel(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in FUEL_TYPES:
        return text
    return FUEL_ALIASES.get(text.lower())


def normalize_transmission(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in TRANSMISSION_TYPES:
        return text
    return TRANSMISSION_ALIASES.get(text.lower())


def format_inr_price(price: float) -> str:
    """Format whole rupees with Indian grouping, e.g. 245000 → ₹2,45,000."""
    amount = int(round(price))
    s = str(amount)
    if len(s) <= 3:
        return f"₹{s}"
    last3 = s[-3:]
    rest = s[:-3]
    groups: list[str] = []
    while rest:
        groups.append(rest[-2:])
        rest = rest[:-2]
    grouped = ",".join(reversed(groups))
    return f"₹{grouped},{last3}"


def format_mileage_km(mileage: float) -> str:
    value = int(round(mileage))
    return f"{value:,} KM".replace(",", ",")  # keep Western thousands; OK for KM line


def _model_display_name(make: str, model: str, variant: str, transmission: str) -> str:
    """Title like: Hyundai Grand i10 Sportz Automatic"""
    parts = [make.strip(), model.strip()]
    variant_clean = variant.strip()
    if variant_clean and variant_clean.lower() not in model.lower():
        if variant_clean.lower() != "base":
            parts.append(variant_clean)
    title = " ".join(parts)
    if transmission == "Automatic" and "automatic" not in title.lower():
        title = f"{title} Automatic"
    return title


DEFAULT_SELLING_POINTS = (
    "Price Slightly Negotiable.",
    "Loan Facility Available.",
)


def default_marketing_blurb(
    make: str,
    model: str,
    variant: str,
    year: int,
    fuel: str,
    transmission: str,
) -> str:
    variant_bit = "" if variant.lower() == "base" else f" {variant}"
    return (
        f"A stylish and comfortable {make} {model}{variant_bit} ({year}) with "
        f"{fuel.lower()} {transmission.lower()} transmission — neat and clean interiors, "
        f"excellent condition, and ready to drive."
    )


def normalize_selling_points(value: Any) -> list[str]:
    points: list[str] = []
    if isinstance(value, str) and value.strip():
        chunks = re.split(r"[\n•\-]+", value)
        points.extend(chunk.strip(" .") + "." for chunk in chunks if chunk.strip())
    elif isinstance(value, list):
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            if not text.endswith("."):
                text = f"{text}."
            points.append(text)

    # Always include the standard PreOwn offer bullets; keep extras from the message
    merged: list[str] = []
    seen: set[str] = set()
    for point in [*DEFAULT_SELLING_POINTS, *points]:
        key = re.sub(r"\s+", " ", point).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(point)
    return merged


def build_preown_description(
    *,
    make: str,
    model: str,
    variant: str,
    year: int,
    mileage: float,
    price: float | None,
    fuel_type: str,
    transmission_type: str,
    owner_history: str | None,
    marketing_blurb: str | None,
    features: str | None,
    condition: str | None,
    highlights: str | None,
    insurance: str | None,
    selling_points: list[str] | None = None,
) -> str:
    title = _model_display_name(make, model, variant, transmission_type)
    blurb = (marketing_blurb or "").strip() or default_marketing_blurb(
        make, model, variant, year, fuel_type, transmission_type
    )
    # Keep blurb as one paragraph without leading emoji (we add ✨)
    blurb = re.sub(r"^✨\s*", "", blurb).strip()

    model_line = f"{model} {variant}".strip()
    if transmission_type == "Automatic" and "automatic" not in model_line.lower():
        model_line = f"{model_line} Automatic".strip()

    lines = [
        f"🚗 {title} | {year} Model | {fuel_type}",
        "",
        f"✨ {blurb}",
        "",
        "🚘 Car Details:",
        "",
        f"Brand: {make}",
        f"Model: {model_line}",
        f"Manufacturing Year: {year}",
    ]
    if owner_history:
        lines.append(f"Ownership: {owner_history}")
    lines.extend(
        [
            f"Fuel Type: {fuel_type}",
            f"Transmission: {transmission_type}",
            f"Kilometres Driven: {format_mileage_km(mileage)}",
        ]
    )
    if insurance:
        lines.append(f"Insurance: {insurance}")
    if features:
        lines.append(f"Features: {features}")
    if condition:
        lines.append(f"Condition: {condition}")
    if highlights:
        lines.append(f"Highlights: {highlights}")
    if price is not None:
        lines.extend(["", f"💰 Asking Price: {format_inr_price(price)}"])

    points = selling_points or list(DEFAULT_SELLING_POINTS)
    if points:
        lines.append("")
        lines.extend(f"• {point}" for point in points)

    lines.extend(
        [
            "",
            "📞 Interested buyers can contact the seller for more details and a test drive.",
            "",
            "🚘 PreOwn – Buy Better. Sell Smarter.",
        ]
    )

    text = "\n".join(lines)
    if len(text) <= 1000:
        return text

    trim_kwargs = dict(
        make=make,
        model=model,
        variant=variant,
        year=year,
        mileage=mileage,
        price=price,
        fuel_type=fuel_type,
        transmission_type=transmission_type,
        owner_history=owner_history,
        marketing_blurb=blurb,
        features=features,
        condition=condition,
        highlights=highlights,
        insurance=insurance,
        selling_points=points,
    )

    # Trim optional lines / blurb to fit CreateCarDto max length (1000)
    if highlights:
        return build_preown_description(**{**trim_kwargs, "highlights": None})
    if features:
        return build_preown_description(
            **{**trim_kwargs, "features": None, "highlights": None}
        )
    if len(points) > len(DEFAULT_SELLING_POINTS):
        return build_preown_description(
            **{**trim_kwargs, "features": None, "highlights": None, "selling_points": list(DEFAULT_SELLING_POINTS)}
        )
    if len(blurb) > 180:
        return build_preown_description(
            **{
                **trim_kwargs,
                "features": None,
                "highlights": None,
                "marketing_blurb": blurb[:180].rstrip() + "...",
                "selling_points": list(DEFAULT_SELLING_POINTS),
            }
        )

    return text[:997].rstrip() + "..."


def _optional_text(raw: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = raw.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def normalize_car_dict(raw: dict[str, Any], index: int) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    car_label = f"car[{index}]"
    data: dict[str, Any] = {}

    for key in ("make", "model", "variant", "location", "ownerHistory", "whenTOSell"):
        if key in raw and raw[key] is not None:
            text = str(raw[key]).strip()
            if text:
                data[key] = text

    if "make" not in data or "model" not in data:
        warnings.append(f"{car_label}: make/model missing; car dropped.")
        return None, warnings

    if "variant" not in data:
        data["variant"] = "Base"
        warnings.append(f"{car_label}: variant missing; defaulted to Base.")
    if "location" not in data:
        data["location"] = "Unknown"
        warnings.append(f"{car_label}: location missing; defaulted to Unknown.")

    year = raw.get("year")
    try:
        data["year"] = int(year)
    except (TypeError, ValueError):
        warnings.append(f"{car_label}: invalid year; car dropped.")
        return None, warnings

    mileage = normalize_mileage(raw.get("mileage"))
    if mileage is None:
        warnings.append(f"{car_label}: invalid mileage; car dropped.")
        return None, warnings
    data["mileage"] = mileage

    fuel = normalize_fuel(raw.get("fuel_type"))
    if fuel is None:
        warnings.append(f"{car_label}: invalid fuel_type; car dropped.")
        return None, warnings
    data["fuel_type"] = fuel

    transmission = normalize_transmission(raw.get("transmission_type"))
    if transmission is None:
        warnings.append(f"{car_label}: invalid transmission_type; car dropped.")
        return None, warnings
    data["transmission_type"] = transmission

    price = normalize_price(raw.get("price"))
    if price is not None:
        data["price"] = price

    marketing_blurb = _optional_text(raw, "marketingBlurb", "description")
    features = _optional_text(raw, "features")
    condition = _optional_text(raw, "condition")
    highlights = _optional_text(raw, "highlights")
    insurance = _optional_text(raw, "insurance")
    selling_points = normalize_selling_points(raw.get("sellingPoints"))

    data["description"] = build_preown_description(
        make=data["make"],
        model=data["model"],
        variant=data["variant"],
        year=data["year"],
        mileage=data["mileage"],
        price=data.get("price"),
        fuel_type=data["fuel_type"],
        transmission_type=data["transmission_type"],
        owner_history=data.get("ownerHistory"),
        marketing_blurb=marketing_blurb,
        features=features,
        condition=condition,
        highlights=highlights,
        insurance=insurance,
        selling_points=selling_points,
    )

    # Never invent images / status / owner
    data.pop("images", None)
    data.pop("status", None)
    data.pop("owner", None)

    return data, warnings


class ParseCarsService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def parse(self, payload: ParseCarsRequest) -> ParseCarsResponse:
        if not self.settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        client_kwargs: dict[str, Any] = {"api_key": self.settings.openai_api_key}
        if self.settings.openai_base_url:
            client_kwargs["base_url"] = self.settings.openai_base_url

        client = OpenAI(**client_kwargs)
        user_prompt = USER_PROMPT_TEMPLATE.format(
            dealer_id=payload.dealerId or "(none)",
            message=payload.message,
        )

        try:
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=self.settings.openai_model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
            raw = response.choices[0].message.content or "{}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenAI parse-cars failed: %s", type(exc).__name__)
            raise RuntimeError("LLM request failed") from exc

        return self._coerce_response(raw)

    def _coerce_response(self, raw: str) -> ParseCarsResponse:
        try:
            parsed = json.loads(strip_markdown_fences(raw))
        except json.JSONDecodeError as exc:
            raise RuntimeError("LLM returned invalid JSON") from exc

        if not isinstance(parsed, dict):
            raise RuntimeError("LLM returned unexpected JSON shape")

        raw_cars = parsed.get("cars") or []
        if not isinstance(raw_cars, list):
            raise RuntimeError("LLM returned cars in unexpected format")

        warnings: list[str] = []
        llm_warnings = parsed.get("warnings") or []
        if isinstance(llm_warnings, list):
            warnings.extend(str(item) for item in llm_warnings if item)

        cars: list[ParsedCar] = []
        current_year = datetime.now(timezone.utc).year
        max_year = current_year + 1

        for index, item in enumerate(raw_cars[:50]):
            if not isinstance(item, dict):
                warnings.append(f"car[{index}]: skipped non-object entry.")
                continue

            normalized, item_warnings = normalize_car_dict(item, index)
            warnings.extend(item_warnings)
            if normalized is None:
                continue

            year = normalized.get("year")
            if not isinstance(year, int) or year < 1900 or year > max_year:
                warnings.append(f"car[{index}]: year out of range; car dropped.")
                continue

            try:
                cars.append(ParsedCar.model_validate(normalized))
            except ValidationError as exc:
                warnings.append(f"car[{index}]: validation failed ({exc.error_count()} issue(s)); dropped.")

        if not cars and raw_cars:
            raise RuntimeError("No valid cars could be extracted from the message")

        return ParseCarsResponse(cars=cars, warnings=warnings)
