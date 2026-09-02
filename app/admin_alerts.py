"""Alertes admin Telegram : 1ere inscription + abo payant. 0 IA."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
PARIS = ZoneInfo("Europe/Paris")

PAID_HINTS = ("starter", "pro", "business", "decouverte", "découverte", "active", "paid", "premium")
TRIAL_HINTS = ("trial", "essai", "free", "gratuit")


def get_supabase() -> Optional[Client]:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


def telegram_send(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or ""
    chat = os.getenv("TELEGRAM_ADMIN_CHAT_ID") or ""
    if not token or not chat:
        print("[AdminAlert] Telegram env manquant")
        return False
    try:
        body = urlencode({"chat_id": chat, "text": text}).encode()
        req = Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            method="POST",
        )
        with urlopen(req, timeout=12) as resp:
            ok = resp.status == 200
        print(f"[AdminAlert] telegram ok={ok}")
        return ok
    except Exception as e:
        print(f"[AdminAlert] telegram: {e}")
        return False


def claim(sb: Client, user_id: str, kind: str) -> bool:
    """True si on a le droit d'envoyer (première fois)."""
    if not user_id:
        return False
    try:
        sb.table("admin_alerts").insert({"user_id": user_id, "kind": kind}).execute()
        return True
    except Exception as e:
        msg = str(e).lower()
        if "duplicate" in msg or "unique" in msg or "23505" in msg:
            return False
        print(f"[AdminAlert] claim: {e}")
        return False


def display_name(prof: dict) -> str:
    for k in ("first_name", "prenom", "full_name", "name", "display_name"):
        v = (prof.get(k) or "").strip()
        if v:
            return v.split()[0]
    email = (prof.get("email") or "").strip()
    return email.split("@")[0] if email else "—"


def hour_now() -> str:
    return datetime.now(PARIS).strftime("%d/%m/%Y %H:%M")


def notify_signup(user_id: str, email: str = "", prenom: str = "") -> dict:
    sb = get_supabase()
    if not sb:
        return {"ok": False, "reason": "no_sb"}
    if not claim(sb, user_id, "signup"):
        return {"ok": True, "skipped": True}
    prenom = prenom or "—"
    email = email or "—"
    text = (
        "🎉 Nouvelle inscription Clarity !\n"
        f"Prénom : {prenom}\n"
        f"Email : {email}\n"
        "Essai gratuit démarré\n"
        f"Heure : {hour_now()}"
    )
    telegram_send(text)
    return {"ok": True, "sent": True}


def notify_paid(user_id: str, email: str = "", prenom: str = "", offre: str = "", prix: str = "") -> dict:
    sb = get_supabase()
    if not sb:
        return {"ok": False, "reason": "no_sb"}
    if not claim(sb, user_id, "paid"):
        return {"ok": True, "skipped": True}
    text = (
        "💳 Nouvel abonnement Clarity !\n"
        f"Prénom : {prenom or '—'}\n"
        f"Email : {email or '—'}\n"
        f"Offre : {offre or '—'}\n"
        f"Prix : {prix or '—'}/mois\n"
        f"Heure : {hour_now()}"
    )
    telegram_send(text)
    return {"ok": True, "sent": True}


def _is_paid_row(row: dict) -> bool:
    blob = " ".join(
        str(row.get(k) or "")
        for k in ("plan", "plan_name", "status", "tier", "price_id", "stripe_status")
    ).lower()
    if any(t in blob for t in TRIAL_HINTS) and "active" not in blob:
        if not any(p in blob for p in ("starter", "pro", "business")):
            return False
    return any(p in blob for p in PAID_HINTS)


def process_admin_alerts(sb: Client, now: datetime, debug: list) -> dict:
    out = {"signups": [], "paid": []}
    since = (now - timedelta(hours=6)).isoformat()
    # Inscriptions récentes
    try:
        rows = (
            sb.table("profiles")
            .select("*")
            .gte("created_at", since)
            .limit(80)
            .execute()
            .data
            or []
        )
    except Exception as e:
        debug.append(f"admin profiles: {e}")
        rows = []
    for p in rows:
        uid = str(p.get("id") or p.get("user_id") or "")
        if not uid:
            continue
        r = notify_signup(uid, p.get("email") or "", display_name(p))
        if r.get("sent"):
            out["signups"].append(p.get("email") or uid)
    # Abos
    try:
        subs = sb.table("subscriptions").select("*").limit(120).execute().data or []
    except Exception as e:
        debug.append(f"admin subs: {e}")
        subs = []
    for s in subs:
        if not _is_paid_row(s):
            continue
        uid = str(s.get("user_id") or s.get("id") or "")
        if not uid:
            continue
        prof = {}
        try:
            pr = sb.table("profiles").select("*").eq("id", uid).limit(1).execute()
            if pr.data:
                prof = pr.data[0]
        except Exception:
            pass
        offre = s.get("plan") or s.get("plan_name") or s.get("tier") or ""
        prix = s.get("price") or s.get("amount") or ""
        r = notify_paid(uid, prof.get("email") or s.get("email") or "", display_name(prof), str(offre), str(prix))
        if r.get("sent"):
            out["paid"].append(uid)
    return out
