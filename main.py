import base64
import time
import uuid
from collections import deque
from threading import Lock

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# ---- Your assigned values ----
TOTAL_ORDERS = 49      # T
RATE_LIMIT = 16        # R requests...
WINDOW_SECONDS = 10    # ...per 10 seconds

app = FastAPI()
lock = Lock()

# =========================================================
# 3. RATE LIMITING (middleware = code that runs on every request)
# =========================================================
client_hits = {}  # client_id -> deque of timestamps of recent requests


@app.middleware("http")
async def rate_limiter(request: Request, call_next):
    client_id = request.headers.get("X-Client-Id")

    # No client id (or a CORS preflight)? Don't rate limit.
    if not client_id or request.method == "OPTIONS":
        return await call_next(request)

    now = time.time()
    with lock:
        hits = client_hits.setdefault(client_id, deque())

        # Forget requests older than 10 seconds
        while hits and now - hits[0] >= WINDOW_SECONDS:
            hits.popleft()

        if len(hits) >= RATE_LIMIT:
            retry_after = max(1, int(WINDOW_SECONDS - (now - hits[0])) + 1)
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests"},
                headers={"Retry-After": str(retry_after)},
            )

        hits.append(now)

    return await call_next(request)


# CORS must be added AFTER the rate limiter so it wraps it.
# That way even 429 responses carry CORS headers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

# =========================================================
# 1. IDEMPOTENT ORDER CREATION
# =========================================================
orders_by_key = {}  # idempotency key -> the order we created


@app.post("/orders", status_code=201)
async def create_order(idempotency_key: str = Header(None, alias="Idempotency-Key")):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header required")

    with lock:
        if idempotency_key in orders_by_key:          # seen before?
            return orders_by_key[idempotency_key]     # same order, no duplicate

        order = {"id": str(uuid.uuid4()), "status": "created"}
        orders_by_key[idempotency_key] = order
        return order


# =========================================================
# 2. CURSOR PAGINATION
# =========================================================
CATALOG = [{"id": i, "name": f"Order {i}"} for i in range(1, TOTAL_ORDERS + 1)]


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"offset:{offset}".encode()).decode()


def decode_cursor(cursor: str) -> int:
    try:
        text = base64.urlsafe_b64decode(cursor.encode()).decode()
        return int(text.split(":")[1])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cursor")


@app.get("/orders")
async def list_orders(limit: int = 10, cursor: str = None):
    if limit < 1:
        raise HTTPException(status_code=400, detail="limit must be >= 1")

    start = decode_cursor(cursor) if cursor else 0
    end = start + limit
    items = CATALOG[start:end]

    next_cursor = encode_cursor(end) if end < len(CATALOG) else None
    return {"items": items, "next_cursor": next_cursor}


@app.get("/")
async def root():
    return {"status": "ok"}
