# Car AI Listing Validator

Independent FastAPI service that:

1. **Validates** used-car listing text + images and returns a recommended status for `car-api`
2. **Parses** WhatsApp-style dealer inventory text into structured car JSON for the Preown admin import UI

This service does **not** save cars or send email. Saving belongs in `car-api`; seller notifications belong in `car-api`.

## Stack

- Python 3.11+
- FastAPI + Uvicorn
- Pydantic v2 / pydantic-settings
- OpenAI Chat Completions (vision for moderation; text for inventory parse)

## Setup

```bash
cd car-ai-validator
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: set API_KEY; set OPENAI_API_KEY for AI features
```

## Run

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8090
```

Docs: http://localhost:8090/docs

### Docker

```bash
docker build -t car-ai-validator .
docker run --rm -p 8090:8090 --env-file .env car-ai-validator
```

## Endpoints

### `GET /health`

No auth. Returns `{ "status": "ok" }`.

### `POST /v1/validate-listing`

Requires header: `X-API-Key: <API_KEY>`

Moderates a listing and returns `riskLevel`, recommended `status`, findings, etc.

### `POST /parse-cars`

No auth (v1 — for local admin / Postman). CORS limited to configured origins.

Turns WhatsApp-style inventory text into structured cars for the admin import page.
Does **not** save cars — frontend/admin calls `car-api` later.

**Request:**

```json
{
  "message": "raw whatsapp text with one or more cars",
  "dealerId": "optional string for context only — do not invent cars from it"
}
```

**Response 200:**

```json
{
  "cars": [
    {
      "make": "Hyundai",
      "model": "Creta",
      "variant": "SX",
      "location": "Pune",
      "year": 2021,
      "mileage": 32000,
      "price": 1450000,
      "fuel_type": "Petrol",
      "transmission_type": "Automatic",
      "description": "Single owner, company service",
      "ownerHistory": "1st owner",
      "whenTOSell": "Immediate"
    }
  ],
  "warnings": ["optional human-readable notes about guesses / missing fields"]
}
```

- Invalid body → `400`
- LLM / config failure → `502` (no secrets in the message)

#### Sample curl

```bash
curl -s -X POST http://localhost:8090/parse-cars \
  -H "Content-Type: application/json" \
  -d '{
    "dealerId": "autonity",
    "message": "Hi, please add these cars for Autonity showroom:\n\n1) Hyundai Creta SX 2021\nPetrol Automatic\n32,000 km\nPrice 14.5 Lakh\nPune\nSingle owner, company service\n\n2) Maruti Swift VXI 2019\nPetrol Manual\n45k km\n₹6,80,000\nHinjewadi\nImmediate sale"
  }'
```

Point the admin frontend at this service:

```env
VITE_AI_IMPORT_URL=http://localhost:8090
```

## Env

| Variable | Required | Description |
|----------|----------|-------------|
| `API_KEY` | yes (for validate) | Shared secret; car-api sends as `X-API-Key` |
| `OPENAI_API_KEY` | yes (for parse / AI validate) | OpenAI-compatible API key |
| `OPENAI_MODEL` | no | Default `gpt-4o-mini` |
| `OPENAI_BASE_URL` | no | Optional OpenAI-compatible base URL |
| `CORS_ORIGINS` | no | Comma-separated origins (default `http://localhost:3000,http://localhost:5173`) |
| `PORT` | no | Default `8090` |

## Connecting from car-api (validate)

1. Set in car-api env:
   - `AI_VALIDATOR_URL=http://localhost:8090`
   - `AI_VALIDATOR_API_KEY=<same as API_KEY here>`
2. After creating a car (`status: active`), POST the listing + image URLs to `/v1/validate-listing`.
3. Apply returned `status`, `flaggedForAdmin`, and store the rest as `moderationResult`.
4. Public browse should only show `status: active`.

### curl smoke test (validate)

```bash
curl -s http://localhost:8090/health

curl -s -X POST http://localhost:8090/v1/validate-listing \
  -H "Content-Type: application/json" \
  -H "X-API-Key: shared-secret-with-car-api" \
  -d '{
    "make":"Honda","model":"City","variant":"VX","year":2020,
    "mileage":40000,"price":750000,"location":"Pune",
    "description":"Clean car","images":[]
  }'
```

## How the three pieces fit (WhatsApp import)

| Piece | Job |
| --- | --- |
| Frontend admin page | Paste message → call AI → show cards → Save |
| **This AI service** (`POST /parse-cars`) | Text → structured `cars[]` JSON |
| car-api | `POST /admin/dealers/:id/cars` saves as `hidden` |

## Project location

```
Cars_preown/
  car-api/
  car-ai-validator/   ← this service
```
