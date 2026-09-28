# Car AI Listing Validator

Independent FastAPI service that validates used-car listing **text + images** and returns a recommended listing status for `car-api` to apply.

This service does **not** send email. Seller notifications (if any) belong in `car-api`.

## Stack

- Python 3.11+
- FastAPI + Uvicorn
- Pydantic v2 / pydantic-settings
- OpenAI Chat Completions (vision), with a local rules fallback when no API key is set

## Setup

```bash
cd car-ai-validator
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: set API_KEY; optionally OPENAI_API_KEY
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

**Request example:**

```json
{
  "carId": "66f...",
  "make": "Honda",
  "model": "City",
  "variant": "VX",
  "year": 2020,
  "mileage": 40000,
  "price": 750000,
  "location": "Pune",
  "description": "Well maintained single owner car.",
  "ownerHistory": "1st owner",
  "whenTOSell": "ASAP",
  "fuel_type": "Petrol",
  "transmission_type": "Manual",
  "images": ["https://your-bucket.s3.amazonaws.com/cars/1.jpg"]
}
```

**Response example:**

```json
{
  "riskLevel": "ok",
  "status": "active",
  "flaggedForAdmin": false,
  "categories": [],
  "score": 0,
  "feedback": "Listing looks fine.",
  "textFindings": [],
  "imageFindings": [],
  "model": "rules-fallback",
  "reviewedAt": "2026-09-18T13:00:00Z"
}
```

### Status mapping

| riskLevel | status | flaggedForAdmin | Meaning |
|-----------|--------|-----------------|---------|
| ok | active | false | Keep live |
| soft | active | true | Keep live + admin queue |
| hard + nsfw | hidden | true | Auto-hide |
| hard (scam/spam) | flagged | true | Auto-hide |

## Env

| Variable | Required | Description |
|----------|----------|-------------|
| `API_KEY` | yes | Shared secret; car-api sends as `X-API-Key` |
| `OPENAI_API_KEY` | no | If empty, rules fallback is used |
| `OPENAI_MODEL` | no | Default `gpt-4o-mini` |

## Connecting from car-api (later)

1. Set in car-api env:
   - `AI_VALIDATOR_URL=http://localhost:8090`
   - `AI_VALIDATOR_API_KEY=<same as API_KEY here>`
2. After creating a car (`status: active`), POST the listing + image URLs to `/v1/validate-listing`.
3. Apply returned `status`, `flaggedForAdmin`, and store the rest as `moderationResult`.
4. Public browse should only show `status: active`.

### curl smoke test

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

## Project location

```
Cars_preown/
  car-api/
  car-ai-validator/   ← this service
```
