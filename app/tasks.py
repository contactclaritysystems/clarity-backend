"""Tâches à cocher (pas de notif cron)."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Optional
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()


def get_supabase() -> Optional[Client]:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


def list_tasks(user_id: str) -> dict:
    sb = get_supabase()
    if not sb or not user_id:
        return {"success": False, "tasks": []}
    try:
        r = (
            sb.table("tasks")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(200)
            .execute()
        )
        return {"success": True, "tasks": r.data or []}
    except Exception as e:
        return {"success": False, "message": str(e), "tasks": []}


def create_task(user_id: str, titre: str, due_date: Optional[str] = None) -> dict:
    sb = get_supabase()
    titre = (titre or "").strip()
    if not sb or not user_id or not titre:
        return {"success": False, "message": "titre / user_id manquant"}
    row = {
        "user_id": user_id,
        "titre": titre,
        "status": "a_faire",
        "due_date": due_date or None,
    }
    try:
        r = sb.table("tasks").insert(row).execute()
        return {"success": True, "task": (r.data or [None])[0]}
    except Exception as e:
        return {"success": False, "message": str(e)}


def update_task(user_id: str, task_id: str, status: Optional[str] = None, titre: Optional[str] = None) -> dict:
    sb = get_supabase()
    if not sb or not user_id or not task_id:
        return {"success": False, "message": "paramètres manquants"}
    patch: Dict[str, Any] = {}
    if status:
        st = status.strip().lower().replace(" ", "_").replace("à", "a")
        if st in ("fait", "done"):
            st = "fait"
        else:
            st = "a_faire"
        patch["status"] = st
    if titre is not None:
        patch["titre"] = titre.strip()
    if not patch:
        return {"success": False, "message": "rien à modifier"}
    try:
        r = sb.table("tasks").update(patch).eq("id", task_id).eq("user_id", user_id).execute()
        if not r.data:
            return {"success": False, "message": "tâche introuvable"}
        return {"success": True, "task": r.data[0]}
    except Exception as e:
        return {"success": False, "message": str(e)}


def delete_task(user_id: str, task_id: str) -> dict:
    sb = get_supabase()
    if not sb or not user_id or not task_id:
        return {"success": False, "message": "paramètres manquants"}
    try:
        r = sb.table("tasks").delete().eq("id", task_id).eq("user_id", user_id).execute()
        return {"success": True, "deleted": bool(r.data)}
    except Exception as e:
        return {"success": False, "message": str(e)}
