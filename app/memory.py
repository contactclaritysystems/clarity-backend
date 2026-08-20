"""
Mémoire business persistante par utilisateur (Supabase).
Faits stables : offre, produit, ton, société — pas tout l'historique de chat.
"""

import json
import os
from datetime import datetime
from typing import Optional, Dict, Any, List
from openai import OpenAI
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")


def get_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY manquante")
    return OpenAI(api_key=api_key)


def get_supabase() -> Optional[Client]:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


def load_memory(user_id: Optional[str]) -> Dict[str, str]:
    """Retourne {key: value} pour cet utilisateur."""
    if not user_id:
        return {}
    sb = get_supabase()
    if not sb:
        return {}
    try:
        res = (
            sb.table("user_business_memory")
            .select("key, value")
            .eq("user_id", user_id)
            .execute()
        )
        out = {}
        for row in res.data or []:
            k, v = row.get("key"), row.get("value")
            if k and v:
                out[str(k)] = str(v)
        return out
    except Exception as e:
        print(f"[Memory] load error: {e}")
        return {}


def memory_as_text(facts: Dict[str, str]) -> str:
    if not facts:
        return ""
    lines = ["Faits connus sur le business de l'utilisateur :"]
    for k, v in sorted(facts.items()):
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)


def upsert_facts(user_id: str, facts: Dict[str, str]) -> None:
    """Crée ou met à jour des faits (remplace la valeur si la clé existe)."""
    if not user_id or not facts:
        return
    sb = get_supabase()
    if not sb:
        return
    now = datetime.utcnow().isoformat() + "Z"
    for key, value in facts.items():
        key = (key or "").strip()[:80]
        value = (value or "").strip()[:2000]
        if not key or not value:
            continue
        try:
            sb.table("user_business_memory").upsert(
                {
                    "user_id": user_id,
                    "key": key,
                    "value": value,
                    "updated_at": now,
                },
                on_conflict="user_id,key",
            ).execute()
        except Exception as e:
            print(f"[Memory] upsert {key} error: {e}")
            # fallback: delete + insert
            try:
                sb.table("user_business_memory").delete().eq("user_id", user_id).eq(
                    "key", key
                ).execute()
                sb.table("user_business_memory").insert(
                    {
                        "user_id": user_id,
                        "key": key,
                        "value": value,
                        "updated_at": now,
                    }
                ).execute()
            except Exception as e2:
                print(f"[Memory] insert fallback error: {e2}")


EXTRACT_SYSTEM = """Tu extrais les FAITS BUSINESS durables d'un échange de rédaction.
Réponds UNIQUEMENT en JSON.

Ne garde que ce qui sera utile plus tard (offre, produit, public, promesse, prix,
durée d'essai, nom de marque, positionnement…).
Ignore les formulations ponctuelles ("plus court", "dernier jour" sans contexte).

Si une info REMPLACE une ancienne (nouvelle offre), utilise la même clé
(ex: current_offer) avec la nouvelle valeur.

{
  "facts": {
    "current_offer": "...",
    "product_name": "...",
    "trial": "7 jours gratuits",
    "audience": "patrons / dirigeants"
  }
}
Clés en snake_case anglais court. Valeurs en français, factuelles.
Si rien de durable : {"facts": {}}
"""


async def extract_and_save_memory(
    user_id: Optional[str],
    instruction: str,
    produced_text: str,
    history: str = "",
) -> None:
    if not user_id:
        return
    try:
        user_msg = f"Demande : {instruction}\n\nTexte produit :\n{produced_text[:1500]}"
        if history:
            user_msg += f"\n\nHistorique :\n{history[-1500:]}"
        response = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        facts = data.get("facts") or {}
        if isinstance(facts, dict) and facts:
            # merge: only non-empty strings
            clean = {
                str(k): str(v).strip()
                for k, v in facts.items()
                if k and v and str(v).strip()
            }
            if clean:
                upsert_facts(user_id, clean)
                print(f"[Memory] saved for {user_id}: {list(clean.keys())}")
    except Exception as e:
        print(f"[Memory] extract error: {e}")
