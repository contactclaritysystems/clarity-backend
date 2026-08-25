"""Enregistrement des idées / besoins utilisateurs."""
import os
from datetime import datetime
from typing import Optional, Dict, Any
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()


def get_supabase() -> Optional[Client]:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


def save_feature_request(
    user_id: Optional[str],
    user_email: Optional[str],
    idea: str,
    source: str = "user",
) -> Dict[str, Any]:
    idea = (idea or "").strip()
    if not idea:
        return {"success": False, "message": "Idée vide."}
    if len(idea) > 2000:
        idea = idea[:2000]
    sb = get_supabase()
    if not sb:
        print(f"[FeatureRequest] (no supabase) {user_id}: {idea[:80]}")
        return {"success": True, "message": "Idée notée.", "stored": False}
    try:
        row = {
            "user_id": user_id or None,
            "user_email": user_email or None,
            "idea": idea,
            "source": source,
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        sb.table("feature_requests").insert(row).execute()
        return {
            "success": True,
            "message": "Merci — votre idée a été transmise à l’équipe Clarity.",
            "stored": True,
        }
    except Exception as e:
        print(f"[FeatureRequest] error: {e}")
        # Ne pas bloquer l’UX si la table n’existe pas encore
        return {
            "success": True,
            "message": "Merci — votre idée a bien été prise en compte.",
            "stored": False,
        }
