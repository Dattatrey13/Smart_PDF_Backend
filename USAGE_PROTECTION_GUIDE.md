# AI Usage Protection System — Architecture Guide

## Overview

A complete, production-grade usage-protection system for the NeuroPDF AI-powered PDF Reader.
All rate limits, quotas, and validations are enforced **server-side** in FastAPI with Firestore
as the persistent store and Firebase App Check for client attestation.

---

## Directory Structure

```
Smart_PDF_Backend/
├── models/
│   ├── __init__.py
│   ├── tier_limits.py          # ★ Single source of truth for FREE / PREMIUM limits
│   └── usage_schemas.py        # Pydantic schemas for Firestore docs & API responses
├── dependencies/
│   ├── __init__.py
│   └── guards.py               # ★ require_ai_access / require_upload_access / require_otp_access
├── services/
│   ├── usage_service.py        # ★ Firestore-backed usage tracking (read/write/reset)
│   ├── background.py           # Fire-and-forget async task manager
│   ├── cache.py                # LRU response & embedding caches
│   └── pdf_processor.py        # PDF extraction pipeline
├── utils/
│   ├── __init__.py
│   └── app_check.py            # ★ Firebase App Check token verification
├── middleware/
│   ├── security.py             # ★ IP rate limiting, IP blocking, security headers
│   ├── logging_mw.py           # Request logging + X-Request-ID
│   └── exceptions.py           # Global HTTP error handlers
├── routers/
│   ├── ai.py                   # /ai/ask, /ai/summary, /ai/search
│   ├── pdf.py                  # /pdf/upload, /pdf/info, /pdf/delete
│   ├── usage.py                # ★ /usage/status (new)
│   └── health.py               # /health, /admin/*
├── auth/
│   ├── dependencies.py         # get_current_user (Firebase ID token)
│   ├── firestore_service.py    # Firestore CRUD for usage, metadata, chat
│   ├── otp_service.py          # OTP generation, hashing, email
│   ├── rate_limiter.py         # Legacy in-memory rate limiter (kept for compat)
│   ├── storage_service.py      # PDF/image upload validation
│   ├── user_service.py         # User profile CRUD
│   └── routes.py               # /auth/* endpoints
└── config.py                   # Env-based settings
```

Files marked with ★ are new or significantly modified.

---

## Tier Definitions (Single Source of Truth)

Defined in `models/tier_limits.py`:

| Limit                      | Free        | Premium      |
|----------------------------|-------------|--------------|
| AI Requests / day          | 20          | 200          |
| Token Budget / day         | 300 K       | 5 M          |
| AI Cooldown                | 8 s         | 2 s          |
| Concurrent AI Jobs         | 1           | 3            |
| AI Processable Pages / day | 50          | 500          |
| PDF Upload Size            | 20 MB       | 100 MB       |
| PDF Max Pages              | 150         | 1 000        |
| Upload Rate / hour         | 10          | 50           |
| OTP Requests / hour        | 5           | 10           |
| OTP Cooldown               | 60 s        | 60 s         |
| Failed OTP Attempts        | 5           | 5            |
| Global IP Rate / min       | 15          | 60           |
| Priority AI Queue          | No          | Yes          |

---

## Firestore Document Structure

### Collection: `ai_usage/{uid}`

```json
{
  "uid": "abc123",
  "subscription_plan": "free",

  "used_today": 5,
  "token_usage_today": 42000,
  "input_tokens_today": 30000,
  "output_tokens_today": 12000,
  "processed_pages_today": 12,
  "last_request_at": "2026-05-13T10:32:00+00:00",

  "upload_count_hour": 2,
  "upload_hour_start": "2026-05-13T10:00:00+00:00",
  "otp_requests_hour": 1,
  "otp_hour_start": "2026-05-13T10:00:00+00:00",

  "concurrent_jobs": 0,

  "ai_daily_limit": 20,
  "token_limit": 300000,
  "reset_at": "2026-05-14T00:00:00+00:00",
  "last_reset_date": "2026-05-13",

  "total_requests": 150,
  "blocked_until": null
}
```

### Collection: `users/{uid}`

```json
{
  "uid": "abc123",
  "email": "user@example.com",
  "subscription_plan": "free",
  "ai_daily_limit": 20,
  "ai_used_today": 5,
  "account_status": "active",
  "last_reset_date": "2026-05-13"
}
```

### Collection: `subscriptions/{uid}`

```json
{
  "uid": "abc123",
  "current_plan": "free",
  "billing_status": "none",
  "expiry_date": null,
  "transaction_id": null
}
```

---

## Validation Layers — Where Each Check Happens

### Layer 1: Flutter Frontend (advisory — never trusted)

| Check                   | Purpose                                                  |
|-------------------------|----------------------------------------------------------|
| File size < 20/100 MB   | Prevent uploading files the server will reject            |
| Page count display       | UX: warn user before upload                              |
| Cooldown timer           | Disable "Send" button for N seconds between requests      |
| Usage counters           | Show remaining quota badges (fetched from `/usage/status`) |
| App Check integration    | Send `X-Firebase-AppCheck` header on every request        |

### Layer 2: FastAPI Middleware (app-wide, runs before routes)

| Middleware                     | Scope           | Error Code |
|--------------------------------|-----------------|------------|
| `GlobalRateLimitMiddleware`    | Per-IP, 15/min  | 429        |
| `RequestSizeLimitMiddleware`   | 105 MB max body | 413        |
| `IPBlockMiddleware`            | Block after 20 auth failures/hr | 403 |
| `SecurityHeadersMiddleware`    | CSP, HSTS, etc. | —          |

### Layer 3: FastAPI Dependencies (per-route guards)

| Dependency              | Used On             | Checks                                            |
|-------------------------|---------------------|----------------------------------------------------|
| `require_ai_access`     | `/ai/*`             | Auth, App Check, cooldown, daily AI limit, token budget, concurrent jobs |
| `require_upload_access`  | `/pdf/upload`       | Auth, App Check, upload rate limit                  |
| `require_otp_access`    | `/auth/signup`, `/auth/resend-otp` | App Check, IP-level rate limit    |

### Layer 4: Route-Level Validation

| Route            | Additional Checks                                      |
|------------------|--------------------------------------------------------|
| `/pdf/upload`    | Tier-based file size (413), page count (413), daily page budget (429) |
| `/ai/ask`        | Token estimation & recording                            |
| `/ai/summary`    | Token estimation & recording                            |

### Layer 5: Firestore Security Rules

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {

    // Users can only read/write their own usage doc
    match /ai_usage/{uid} {
      allow read: if request.auth != null && request.auth.uid == uid;
      allow write: if false;  // Only backend writes via Admin SDK
    }

    // Users can read their own profile
    match /users/{uid} {
      allow read: if request.auth != null && request.auth.uid == uid;
      allow write: if false;  // Backend-managed
    }

    match /subscriptions/{uid} {
      allow read: if request.auth != null && request.auth.uid == uid;
      allow write: if false;
    }
  }
}
```

### Layer 6: Firebase App Check

- Configured in Firebase Console for Android (Play Integrity), iOS (App Attest), Web (reCAPTCHA Enterprise).
- Client sends `X-Firebase-AppCheck` header.
- Backend verifies via `firebase_admin.app_check.verify_token()`.
- Rejects requests from spoofed/non-genuine clients.
- Disable in dev with `APP_CHECK_ENABLED=false`.

---

## HTTP Error Codes

| Code | Meaning               | When                                             |
|------|-----------------------|--------------------------------------------------|
| 401  | Unauthorized          | Missing/invalid Firebase ID token                 |
| 403  | Forbidden             | Account suspended, invalid App Check, IP blocked  |
| 413  | Payload Too Large     | File size or page count exceeds tier limit         |
| 429  | Too Many Requests     | Any rate/quota limit exceeded                     |
| 500  | Internal Server Error | Unexpected failures                                |

All 429 responses include a structured body:

```json
{
  "detail": "Daily AI limit reached (20/20). Resets at 2026-05-14T00:00:00+00:00.",
  "error_code": "DAILY_AI_LIMIT",
  "current": 20,
  "limit": 20,
  "reset_at": "2026-05-14T00:00:00+00:00",
  "retry_after": null
}
```

---

## Request Flow (AI Endpoint)

```
Client → GlobalRateLimitMiddleware (IP check)
       → SecurityHeadersMiddleware
       → RequestSizeLimitMiddleware
       → IPBlockMiddleware
       → CORS
       → Route: /ai/ask
           → Depends(require_ai_access)
               → get_current_user()        # Firebase ID token
               → verify_app_check()        # App Check header
               → _resolve_plan(uid)        # Firestore lookup
               → check_cooldown()          # Firestore last_request_at
               → check_ai_request_allowed()# Firestore used_today
               → check_token_budget()      # Firestore token_usage_today
               → acquire_job_slot()        # Firestore concurrent_jobs +1
           → Process question
           → Background:
               → record_ai_request()       # Firestore increments
               → release_job_slot()        # Firestore concurrent_jobs -1
               → save_chat_entry()
       → Response
```

---

## Best Practices Implemented

### AI Cost Control
- **Token budget**: Hard daily cap (300K free / 5M premium) prevents runaway costs.
- **Caching**: Identical queries return cached responses (1h TTL) — no LLM call.
- **Chunk limiting**: Only top-5 relevant chunks sent to LLM, not entire PDFs.
- **Token estimation**: Input/output tokens tracked per request for cost monitoring.

### Abuse Prevention
- **5-layer defence**: Middleware → Dependencies → Route → Firestore → App Check.
- **Concurrent job limit**: Prevents parallel-request abuse (1 free / 3 premium).
- **IP blocking**: 20 auth failures/hour → 1-hour block.
- **OTP brute-force**: 5 attempts max, then OTP is invalidated.
- **Cooldown**: 8s (free) / 2s (premium) between AI calls.

### Firebase Security
- **Admin SDK only** writes to `ai_usage` and `subscriptions` — clients cannot tamper.
- **Firestore rules** restrict client reads to own documents.
- **App Check** rejects requests from non-genuine app instances.
- **Token revocation** checked on every `verify_firebase_token()` call.

### Token Optimization
- **Response caching**: SHA-256 cache keys, LRU eviction, 1h TTL.
- **Embedding caching**: 24h TTL for repeated embedding requests.
- **Chunk size tuning**: 400 words/chunk balances context quality vs. cost.
- **Background processing**: Usage increments are fire-and-forget (don't block response).

---

## Environment Variables

| Variable                       | Default  | Description                          |
|--------------------------------|----------|--------------------------------------|
| `GLOBAL_RATE_LIMIT_PER_MINUTE` | `15`     | Free-tier IP rate limit              |
| `PREMIUM_IP_RATE_PER_MINUTE`   | `60`     | Premium IP rate limit                |
| `AI_REQUEST_COOLDOWN_SECONDS`  | `8`      | Free-tier cooldown                   |
| `MAX_AI_REQUESTS_FREE_DAILY`   | `20`     | Free-tier daily AI requests          |
| `MAX_PDF_PAGES`                | `150`    | Free-tier max PDF pages              |
| `APP_CHECK_ENABLED`            | `true`   | Enable/disable Firebase App Check    |
| `ADMIN_API_KEY`                | `""`     | Admin endpoint auth key              |

---

## New API Endpoint

### `GET /usage/status`

**Auth**: Bearer token required.

**Response** (`200 OK`):

```json
{
  "plan": "free",
  "ai_requests_used": 5,
  "ai_requests_limit": 20,
  "tokens_used": 42000,
  "tokens_limit": 300000,
  "pages_processed": 12,
  "pages_limit": 50,
  "uploads_this_hour": 2,
  "uploads_limit": 10,
  "concurrent_jobs": 0,
  "concurrent_limit": 1,
  "cooldown_seconds": 8,
  "reset_at": "2026-05-14T00:00:00+00:00",
  "blocked_until": null
}
```

Use this in Flutter to display usage badges and disable buttons when limits are near.
