import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from dotenv import load_dotenv
from urllib.parse import urlparse
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()

from scraper.prices import fetch_prices              # noqa: E402
from scraper.status_checker import run_status_check  # noqa: E402

CHECK_INTERVAL_MINUTES = 10


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_status_check,
        trigger='interval',
        minutes=CHECK_INTERVAL_MINUTES,
        id='status_check',
        max_instances=1,       # never overlap — wait for previous run to finish
        coalesce=True,         # skip missed fires if the server was paused
    )
    scheduler.start()
    print(f"[scheduler] started — publication status check every {CHECK_INTERVAL_MINUTES} minutes")
    yield
    scheduler.shutdown(wait=False)
    print("[scheduler] stopped")


_CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:5173",
).split(",") if o.strip()]

app = FastAPI(title="Greenlamp Publisher API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def extract_domain(url: str) -> str:
    url = url.strip()
    if url.startswith('http'):
        parsed = urlparse(url)
        domain = parsed.netloc
    else:
        domain = url
    domain = domain.replace('www.', '')
    domain = domain.split('/')[0]
    return domain.strip()


@app.get("/")
def health_check():
    return {"status": "ok"}


class PricesRequest(BaseModel):
    magazine: str    # magazine domain, e.g. "investing.com"
    client_name: str # Links.me project name, e.g. "echo.ai"


@app.post("/api/prices")
async def get_prices(req: PricesRequest):
    """
    Scrape PressWhizz and Links.me concurrently for the magazine domain.
    Returns: { presswhizz: int|null, linksme: int|null, errors?: {...} }
    Playwright is synchronous so we offload to a thread pool.
    """
    print(f"[api/prices] received magazine={req.magazine!r} client_name={req.client_name!r}")
    if not req.magazine or not req.client_name:
        raise HTTPException(status_code=422, detail="magazine and client_name are required")
    try:
        magazine = extract_domain(req.magazine).lower()
        client_name = req.client_name.strip()
        print(f"[api/prices] calling fetch_prices({magazine!r}, {client_name!r})")
        result = await run_in_threadpool(
            fetch_prices,
            magazine,
            client_name,
        )
        print(f"[api/prices] result={result!r}")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
