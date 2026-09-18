"""Comptage anonyme des ouvertures de la page d'accueil + Telegram admin."""
import os
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import APIRouter, Query
from supabase import create_client, Client

from app.admin_alerts import telegram_send

load_dotenv()

router = APIRouter()
PARIS = ZoneInfo("Europe/Paris")


def get_supabase() -> Optional[Client]:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


@router.post("/track")
async def track(payload: dict | None = None):
    body = payload or {}
    event = str(body.get("event") or "unknown")[:80]
    src = body.get("src")
    if src is not None:
        src = str(src)[:80]
    sb = get_supabase()
    if not sb:
        return {"ok": False}
    try:
        sb.table("landing_events").insert({"event": event, "src": src}).execute()
    except Exception:
        return {"ok": False}

    total = 0
    try:
        rows = sb.table("landing_events").select("event").execute()
        total = sum(
            1
            for r in (rows.data or [])
            if r.get("event") in ("home_landing", "flyer_landing")
        )
    except Exception:
        pass

    heure = datetime.now(PARIS).strftime("%H:%M")
    telegram_send(
        f"👀 Ouverture Clarity\n"
        f"Page : accueil\n"
        f"Heure : {heure}\n"
        f"Total ouvertures : {total}"
    )
    return {"ok": True}


@router.get("/track/stats")
async def track_stats(secret: str = Query("")):
    if secret != os.getenv("CRON_SECRET", ""):
        return {"ok": False, "message": "secret"}
    sb = get_supabase()
    if not sb:
        return {"ok": False, "message": "supabase"}
    try:
        rows = sb.table("landing_events").select("event").execute()
        data = rows.data or []
    except Exception as e:
        return {"ok": False, "message": str(e)}
    home = sum(1 for r in data if r.get("event") in ("home_landing", "flyer_landing"))
    return {"ok": True, "total": len(data), "home_landing": home}
