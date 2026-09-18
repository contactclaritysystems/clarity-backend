"""Comptage anonyme des ouvertures de la page d'accueil."""
import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Query
from supabase import create_client, Client

load_dotenv()

router = APIRouter()


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
