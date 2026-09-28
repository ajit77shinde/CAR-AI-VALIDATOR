from fastapi import APIRouter, Depends

from app.auth import require_api_key
from app.schemas import ValidateListingRequest, ValidateListingResponse
from app.services.moderator import ModeratorService

router = APIRouter(prefix="/v1", tags=["validate"])


@router.post(
    "/validate-listing",
    response_model=ValidateListingResponse,
    dependencies=[Depends(require_api_key)],
)
async def validate_listing(
    body: ValidateListingRequest,
) -> ValidateListingResponse:
    service = ModeratorService()
    return await service.validate(body)
