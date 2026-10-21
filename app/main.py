import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.routers import health, parse, validate

logging.basicConfig(level=logging.INFO)

settings = get_settings()

app = FastAPI(
    title="Car AI Listing Validator",
    description=(
        "Independent FastAPI service for used-car listing moderation "
        "and WhatsApp inventory parsing for Preown admin import."
    ),
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = exc.errors()
    first = errors[0] if errors else {}
    loc = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    msg = first.get("msg", "Invalid request")
    detail = f"{loc}: {msg}" if loc else msg
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": detail},
    )


app.include_router(health.router)
app.include_router(validate.router)
app.include_router(parse.router)
