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
    """Retourne tous les styles : défauts + personnalisés.
    Pour une même key, la ligne en BASE gagne toujours.
    """
    if not user_id:
        return [
            {
                "id": None,
                "key": s["key"],
                "label": s["label"],
                "example_message": s["example_message"],
                "opening": s.get("opening") or "",
                "closing": s.get("closing") or "",
            }
            for s in DEFAULT_STYLES
        ]

    sb = get_supabase()
    db_by_key = {}
    if sb:
        try:
            res = (
                sb.table("user_writing_styles")
                .select("id, key, label, example_message, opening, closing, updated_at")
                .eq("user_id", user_id)
                .execute()
            )
            for row in res.data or []:
                k = (row.get("key") or "").strip().lower()
                if k:
                    db_by_key[k] = row
            print(f"[Styles] list user={user_id} db_keys={list(db_by_key.keys())}")
        except Exception as e:
            print(f"[Styles] list error: {e}")

    # Seed les keys manquantes (sans écraser)
    if sb and user_id:
        missing = [s for s in DEFAULT_STYLES if s["key"] not in db_by_key]
        if missing:
            seed_defaults(user_id)
            try:
                res = (
                    sb.table("user_writing_styles")
                    .select("id, key, label, example_message, opening, closing, updated_at")
                    .eq("user_id", user_id)
                    .execute()
                )
                db_by_key = {}
                for row in res.data or []:
                    k = (row.get("key") or "").strip().lower()
                    if k:
                        db_by_key[k] = row
            except Exception as e:
                print(f"[Styles] list after seed: {e}")

    out = []
    seen = set()
    # 1) Défauts, écrasés par DB si présent
    for s in DEFAULT_STYLES:
        k = s["key"]
        if k in db_by_key:
            out.append(db_by_key[k])
        else:
            out.append(
                {
                    "id": None,
                    "key": k,
                    "label": s["label"],
                    "example_message": s["example_message"],
                    "opening": s.get("opening") or "",
                    "closing": s.get("closing") or "",
                }
            )
        seen.add(k)
    # 2) Styles custom (cousin, etc.)
    for k, row in db_by_key.items():
        if k not in seen:
            out.append(row)
    out.sort(key=lambda r: (r.get("label") or r.get("key") or "").lower())
    return out


def seed_defaults(user_id: str) -> None:
    sb = get_supabase()
    if not sb or not user_id:
        return
    now = datetime.utcnow().isoformat() + "Z"
    try:
        existing = (
            sb.table("user_writing_styles")
            .select("key")
            .eq("user_id", user_id)
            .execute()
        )
        have = {r["key"] for r in (existing.data or []) if r.get("key")}
    except Exception as e:
        print(f"[Styles] seed list: {e}")
        have = set()

    for s in DEFAULT_STYLES:
        if s["key"] in have:
            continue  # ne jamais écraser un style déjà modifié
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
        except Exception as e:
            print(f"[Styles] seed insert {s['key']}: {e}")


def create_style(user_id: str, data: dict) -> Dict[str, Any]:
    """Crée OU met à jour un style. Toujours re-lit la ligne après écriture."""
    sb = get_supabase()
    if not sb or not user_id:
        return {"success": False, "message": "Supabase / user_id manquant"}

    label = (data.get("label") or "").strip()
    if not label:
        return {"success": False, "message": "Le nom du style est obligatoire"}

    style_id = (data.get("id") or data.get("style_id") or "").strip() or None
    key = (data.get("key") or "").strip().lower()
    if not key:
        import re as _re
        key = _re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40] or "style"
    else:
        import re as _re
        key = _re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")[:40] or key

    fields = {
        "label": label,
        "example_message": (data.get("example_message") or "").strip(),
        "opening": (data.get("opening") or "").strip(),
        "closing": (data.get("closing") or "").strip(),
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }

    print(f"[Styles] SAVE user={user_id} id={style_id} key={key} msg={fields['example_message'][:80]!r}")

    def fetch_by_id(sid):
        r = sb.table("user_writing_styles").select("*").eq("id", sid).limit(1).execute()
        return (r.data or [None])[0]

    def fetch_by_key(uid, k):
        r = (
            sb.table("user_writing_styles")
            .select("*")
            .eq("user_id", uid)
            .eq("key", k)
            .limit(1)
            .execute()
        )
        return (r.data or [None])[0]

    try:
        # ----- UPDATE par id (le plus fiable) -----
        if style_id:
            # Pas de filtre user_id : l'id est unique
            sb.table("user_writing_styles").update(fields).eq("id", style_id).execute()
            row = fetch_by_id(style_id)
            print(f"[Styles] after update by id → {row}")
            if row and row.get("example_message") == fields["example_message"] and row.get("label") == fields["label"]:
                return {"success": True, "style": row, "updated": True}
            # force write via upsert-like: update all text cols again
            if row:
                sb.table("user_writing_styles").update(fields).eq("id", style_id).execute()
                row = fetch_by_id(style_id)
                if row and row.get("example_message") == fields["example_message"]:
                    return {"success": True, "style": row, "updated": True}
                return {
                    "success": False,
                    "message": "La base n'a pas enregistré la modification (clé Supabase ?).",
                    "style": row,
                    "debug": {"wanted": fields, "got": row},
                }

        # ----- UPDATE / INSERT par key -----
        existing = fetch_by_key(user_id, key)
        print(f"[Styles] existing by key → {existing}")

        if existing and existing.get("id"):
            sid = existing["id"]
            sb.table("user_writing_styles").update(fields).eq("id", sid).execute()
            row = fetch_by_id(sid)
            print(f"[Styles] after update by key id={sid} → {row}")
            if row and row.get("example_message") == fields["example_message"]:
                return {"success": True, "style": row, "updated": True}
            return {
                "success": False,
                "message": "Update par key non persisté.",
                "debug": {"wanted": fields, "got": row},
            }

        # ----- INSERT -----
        ins_row = {"user_id": user_id, "key": key, **fields}
        ins = sb.table("user_writing_styles").insert(ins_row).execute()
        print(f"[Styles] insert → {ins.data}")
        if ins.data:
            return {"success": True, "style": ins.data[0], "updated": False}
        row = fetch_by_key(user_id, key)
        if row:
            return {"success": True, "style": row, "updated": False}
        return {"success": False, "message": "Insert échoué"}
    except Exception as e:
        print(f"[Styles] ERROR {e}")
        import traceback
        traceback.print_exc()
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
    """Ton / ouverture / fin = UNIQUEMENT example_message."""
    if not style:
        return ""
    label = style.get("label") or style.get("key") or "style"
    example = (style.get("example_message") or "").strip()
    lines = [
        "STYLE D'ÉCRITURE OBLIGATOIRE : " + label,
    ]
    if example:
        lines.append("Exemple de référence (à imiter pour le TON uniquement) :")
        lines.append("« " + example + " »")
        # detect vous vs tu roughly for explicit instruction
        low = example.lower()
        if any(w in low for w in ["vous", "votre", "vos", "seriez", "pouvez", "souhaitez"]):
            lines.append("Cet exemple VOUVOIE. Le message DOIT vouvoyer (vous/votre). INTERDIT de tutoyer.")
        elif any(w in low for w in [" tu ", " t'", "ton ", "ta ", "tes ", "salut"]):
            lines.append("Cet exemple TUTOIE. Le message DOIT tutoyer (tu/ton).")
        lines.append("Reprendre le même niveau de formalité (Bonjour vs Salut, Cordialement vs À plus).")
    lines.append("N'invente aucun contenu : seulement le ton de l'exemple.")
    return "\n".join(lines)




