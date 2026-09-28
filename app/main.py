import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import health, validate

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Car AI Listing Validator",
    description="Independent FastAPI service that validates used-car listing text and images.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(validate.router)
