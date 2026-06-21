import logging

from fastapi import FastAPI, HTTPException, Request

from config import load_config
from handlers import handle_event

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
log = logging.getLogger("jira-sync")

cfg = load_config()
app = FastAPI(title="jira-sync")


@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    event = payload.get("webhookEvent", "<unknown>")
    log.info("received event: %s", event)
    try:
        handle_event(event, payload, cfg)
    except Exception:
        log.exception("handler failed for %s", event)
        raise HTTPException(status_code=500, detail="handler error")
    return {"ok": True}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
