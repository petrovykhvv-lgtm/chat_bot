"""FastAPI-сервер: статический интерфейс чата и `/api/chat`."""

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import agent_runtime
import bot_flow
import config
import db
import logging_setup
import ratelimit
import sessions

logging_setup.setup()
log = logging.getLogger("server")
STATIC_DIR = config.BASE_DIR / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    purged = db.purge_expired()  # срок хранения данных и устаревшие диалоги
    mode = "demo (no model)" if agent_runtime.is_mock() else f"model={config.AI_MODEL}"
    log.info("started: db=%s ai=%s purged=%s", config.DB_PATH.name, mode, purged)
    yield


app = FastAPI(title="Vector chat bot", lifespan=lifespan, docs_url=None, redoc_url=None)


class ChatIn(BaseModel):
    session_id: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")
    text: str | None = Field(default=None, max_length=config.MAX_MESSAGE_LEN)
    action: str | None = Field(default=None, max_length=64)


@app.post("/api/chat")
def chat(body: ChatIn, request: Request):
    # Обычная `def`: блокирующий вызов модели уходит в пул потоков.
    ip = request.client.host if request.client else "unknown"
    if not ratelimit.allow_ip(ip):
        raise HTTPException(status_code=429, detail="Too many requests", headers={"Retry-After": "60"})
    session = sessions.store.get(body.session_id)
    reply = bot_flow.handle(session, body.text, body.action)
    sessions.store.save(session)
    return reply


@app.get("/api/health")
def health():
    return {"status": "ok", "ai": "demo" if agent_runtime.is_mock() else "model"}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/privacy")
def privacy():
    return FileResponse(STATIC_DIR / "privacy.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_config=None)
