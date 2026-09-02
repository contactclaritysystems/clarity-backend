"""Fiche contact enrichie + last_interaction."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()


def get_supabase() -> Optional[Client]:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


def touch_contact(contact_id: str) -> None:
    if not contact_id:
        return
    sb = get_supabase()
    if not sb:
        return
    try:
        sb.table("contacts").update(
            {"last_interaction_at": datetime.utcnow().isoformat() + "Z"}
        ).eq("id", contact_id).execute()
    except Exception as e:
        print(f"[Contact] touch: {e}")


def get_activity(user_id: str, contact_id: str) -> dict:
    sb = get_supabase()
    if not sb or not user_id or not contact_id:
        return {"success": False, "message": "paramètres manquants"}
    contact = None
    try:
        r = (
            sb.table("contacts")
            .select("*")
            .eq("id", contact_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        contact = (r.data or [None])[0]
    except Exception as e:
        return {"success": False, "message": str(e)}
    if not contact:
        return {"success": False, "message": "contact introuvable"}
    name = (contact.get("full_name") or "").strip()
    rdvs, rappels = [], []
    try:
        q = sb.table("appointments").select("*").eq("user_id", user_id).limit(80)
        rows = q.execute().data or []
        for row in rows:
            if str(row.get("contact_id") or "") == str(contact_id):
                rdvs.append(row)
            elif name and name.lower() in str(row.get("contact_name") or "").lower():
                rdvs.append(row)
        rdvs = rdvs[:20]
    except Exception as e:
        print(f"[Contact] rdvs: {e}")
    try:
        rows = sb.table("follow_ups").select("*").eq("user_id", user_id).limit(80).execute().data or []
        for row in rows:
            if str(row.get("contact_id") or "") == str(contact_id):
                rappels.append(row)
            elif name and name.lower() in str(row.get("contact_name") or "").lower():
                rappels.append(row)
        rappels = rappels[:20]
    except Exception as e:
        print(f"[Contact] rappels: {e}")
    mails = []
    email = (contact.get("email") or "").strip().lower()
    try:
        rows = (
            sb.table("sent_mails")
            .select("id, user_id, contact_id, to_email, subject, sent_at")
            .eq("user_id", user_id)
            .order("sent_at", desc=True)
            .limit(80)
            .execute()
            .data
            or []
        )
        for row in rows:
            if str(row.get("contact_id") or "") == str(contact_id):
                mails.append(row)
            elif email and str(row.get("to_email") or "").strip().lower() == email:
                mails.append(row)
        mails = mails[:20]
    except Exception as e:
        print(f"[Contact] mails: {e}")
    return {
        "success": True,
        "contact": contact,
        "appointments": rdvs,
        "follow_ups": rappels,
        "mails": mails,
    }


def patch_contact(user_id: str, contact_id: str, tags=None, notes_internes=None) -> dict:
    sb = get_supabase()
    if not sb or not user_id or not contact_id:
        return {"success": False, "message": "paramètres manquants"}
    patch: dict[str, Any] = {}
    if tags is not None:
        patch["tags"] = tags if isinstance(tags, list) else [tags]
    if notes_internes is not None:
        patch["notes_internes"] = notes_internes
    if not patch:
        return {"success": False, "message": "rien à modifier"}
    try:
        r = (
            sb.table("contacts")
            .update(patch)
            .eq("id", contact_id)
            .eq("user_id", user_id)
            .execute()
        )
        if not r.data:
            return {"success": False, "message": "sauvegarde bloquée"}
        return {"success": True, "contact": r.data[0]}
    except Exception as e:
        return {"success": False, "message": str(e)}


def log_sent_mail(user_id: str, subject: str, to_email: str = "", contact_id: str = "") -> dict:
    sb = get_supabase()
    subject = (subject or "").strip() or "(sans objet)"
    if not sb or not user_id:
        return {"success": False, "message": "paramètres manquants"}
    row = {
        "user_id": user_id,
        "subject": subject[:200],
        "to_email": (to_email or "").strip().lower() or None,
        "contact_id": contact_id or None,
        "sent_at": datetime.utcnow().isoformat() + "Z",
    }
    try:
        r = sb.table("sent_mails").insert(row).execute()
        if contact_id:
            touch_contact(contact_id)
        return {"success": True, "mail": (r.data or [None])[0]}
    except Exception as e:
        return {"success": False, "message": str(e)}
