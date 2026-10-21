from fastapi import APIRouter, HTTPException, status

from app.schemas import ParseCarsRequest, ParseCarsResponse
from app.services.parse_cars import ParseCarsService

router = APIRouter(tags=["parse"])


@router.post("/parse-cars", response_model=ParseCarsResponse)
async def parse_cars(body: ParseCarsRequest) -> ParseCarsResponse:
    """Turn WhatsApp-style inventory text into structured cars for admin import."""
    service = ParseCarsService()
    try:
        return await service.parse(body)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
