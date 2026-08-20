"""
Styles d'écriture par utilisateur (frère, patron, cousin…).
Utilisés par rédaction (et plus tard mail).
"""

import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

DEFAULT_STYLES = [
    {
        "key": "frere_soeur",
        "label": "Frère / Sœur",
        "example_message": "Viens manger ce soir à la maison",
        "opening": "Salut",
        "closing": "À plus",
    },
    {
        "key": "ami",
        "label": "Ami",
        "example_message": "Salut, tu viens manger ce soir ?",
        "opening": "Salut",
        "closing": "À tout",
    },
    {
        "key": "collegue",
        "label": "Collègue",
        "example_message": "Tu serais dispo pour un point demain matin ?",
        "opening": "Hello",
        "closing": "Bonne journée",
    },
    {
        "key": "patron",
        "label": "Patron",
        "example_message": "Bonjour, seriez-vous disponible pour en discuter demain ?",
        "opening": "Bonjour",
        "closing": "Cordialement",
    },
    {
        "key": "client",
        "label": "Client",
        "example_message": "Bonjour, je me permets de vous contacter concernant notre rendez-vous.",
        "opening": "Bonjour",
        "closing": "Cordialement",
    },
]


def get_supabase() -> Optional[Client]:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


def list_styles(user_id: str) -> List[Dict[str, Any]]:
    if not user_id:
        return list(DEFAULT_STYLES)
    sb = get_supabase()
    if not sb:
        return list(DEFAULT_STYLES)
    try:
        res = (
            sb.table("user_writing_styles")
            .select("id, key, label, example_message, opening, closing, created_at")
            .eq("user_id", user_id)
            .order("label")
            .execute()
        )
        rows = res.data or []
        if not rows:
            # seed defaults once
            seed_defaults(user_id)
            res = (
                sb.table("user_writing_styles")
                .select("id, key, label, example_message, opening, closing, created_at")
                .eq("user_id", user_id)
                .order("label")
                .execute()
            )
            rows = res.data or []
        return rows
    except Exception as e:
        print(f"[Styles] list error: {e}")
        return list(DEFAULT_STYLES)


def seed_defaults(user_id: str) -> None:
    sb = get_supabase()
    if not sb or not user_id:
        return
    now = datetime.utcnow().isoformat() + "Z"
    for s in DEFAULT_STYLES:
        try:
            sb.table("user_writing_styles").upsert(
                {
                    "user_id": user_id,
                    "key": s["key"],
                    "label": s["label"],
                    "example_message": s["example_message"],
                    "opening": s.get("opening") or "",
                    "closing": s.get("closing") or "",
                    "updated_at": now,
                },
                on_conflict="user_id,key",
            ).execute()
        except Exception as e:
            print(f"[Styles] seed {s['key']}: {e}")
            try:
                sb.table("user_writing_styles").insert(
                    {
                        "user_id": user_id,
                        "key": s["key"],
                        "label": s["label"],
                        "example_message": s["example_message"],
                        "opening": s.get("opening") or "",
                        "closing": s.get("closing") or "",
                        "updated_at": now,
                    }
                ).execute()
            except Exception as e2:
                print(f"[Styles] seed insert fail: {e2}")


def create_style(user_id: str, data: dict) -> Dict[str, Any]:
    sb = get_supabase()
    if not sb or not user_id:
        return {"success": False, "message": "Supabase / user_id manquant"}

    label = (data.get("label") or "").strip()
    if not label:
        return {"success": False, "message": "Le nom du style est obligatoire"}

    key = (data.get("key") or "").strip()
    if not key:
        import re
        key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40] or "style"

    row = {
        "user_id": user_id,
        "key": key,
        "label": label,
        "example_message": (data.get("example_message") or "").strip(),
        "opening": (data.get("opening") or "").strip(),
        "closing": (data.get("closing") or "").strip(),
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    try:
        res = sb.table("user_writing_styles").upsert(row, on_conflict="user_id,key").execute()
        if res.data:
            return {"success": True, "style": res.data[0]}
        # fallback select
        sel = (
            sb.table("user_writing_styles")
            .select("*")
            .eq("user_id", user_id)
            .eq("key", key)
            .limit(1)
            .execute()
        )
        if sel.data:
            return {"success": True, "style": sel.data[0]}
        return {"success": True, "style": row}
    except Exception as e:
        print(f"[Styles] create error: {e}")
        return {"success": False, "message": str(e)}


def get_style(user_id: str, style_key: str = None, style_id: str = None) -> Optional[dict]:
    if not user_id:
        return None
    sb = get_supabase()
    if not sb:
        return None
    try:
        q = sb.table("user_writing_styles").select("*").eq("user_id", user_id)
        if style_id:
            q = q.eq("id", style_id)
        elif style_key:
            q = q.eq("key", style_key)
        else:
            return None
        res = q.limit(1).execute()
        return (res.data or [None])[0]
    except Exception as e:
        print(f"[Styles] get error: {e}")
        return None


def style_prompt_block(style: Optional[dict]) -> str:
    if not style:
        return ""
    parts = [f"Style d'écriture choisi : {style.get('label') or style.get('key')}"]
    if style.get("example_message"):
        parts.append(f"Exemple type de l'utilisateur : « {style['example_message']} »")
    if style.get("opening"):
        parts.append(f"Il commence souvent par : « {style['opening']} »")
    if style.get("closing"):
        parts.append(f"Il termine souvent par : « {style['closing']} »")
    parts.append(
        "Imite CE style (tutoiement/vouvoiement, longueur, familiarité) "
        "sans copier l'exemple mot à mot, et sans inventer de contenu."
    )
    return "\n".join(parts)
