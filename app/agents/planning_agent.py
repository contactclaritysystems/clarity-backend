"""
Agent Planning Clarity — Premium
Crée / comprend des rendez-vous à partir d'une dictée.
"""

import json
import os
import re
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
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


def update_progress(request_id: Optional[str], status: str, message: str = ""):
    if not request_id:
        return
    sb = get_supabase()
    if not sb:
        return
    try:
        sb.table("agent_progress").insert({
            "request_id": request_id,
            "agent": "planning",
            "status": status,
            "message": message or ""
        }).execute()
    except Exception as e:
        print(f"[Planning Progress] {e}")


INTENT_SYSTEM = """Tu es le module Planning de Clarity Systems (SaaS français premium).

Extrais les infos d'une dictée pour créer un rendez-vous.
Date de référence (aujourd'hui) : {today}
Réponds UNIQUEMENT en JSON.

Règles dates (français) :
- "demain" → date de demain
- "après-demain" → +2 jours
- "lundi/mardi/..." → prochain jour de la semaine
- "dans 3 jours" → +3 jours
- "le 25" → prochain 25 du mois (ou ce mois si pas passé)
- Heure par défaut si absente : 10:00
- Format date : YYYY-MM-DD
- Format heure : HH:MM (24h)

{
  "contact_name": "prénom ou nom ou null",
  "title": "motif court du RDV",
  "description": "détails éventuels",
  "appointment_date": "YYYY-MM-DD",
  "appointment_time": "HH:MM",
  "has_enough_info": true,
  "missing": null,
  "raw_summary": "résumé clair"
}

has_enough_info = false si date manquante complètement.
Si seule l'heure manque, mets 10:00 et has_enough_info = true.
"""


async def extract_planning_intent(instruction: str, user_name: str) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    # Also give weekday for better French parsing
    weekdays = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    weekday = weekdays[datetime.now().weekday()]
    system = INTENT_SYSTEM.format(today=f"{today} ({weekday})")

    response = get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"Dictée : {instruction}\nUtilisateur : {user_name}"}
        ],
        temperature=0.1,
        max_tokens=400,
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)


def search_contacts(name: str, user_id: Optional[str] = None) -> List[dict]:
    sb = get_supabase()
    if not sb or not name:
        return []
    try:
        query = sb.table("contacts").select("id, full_name, email, company")
        if user_id:
            query = query.eq("user_id", user_id)
        query = query.ilike("full_name", f"%{name.strip()}%")
        result = query.limit(5).execute()
        return result.data or []
    except Exception as e:
        print(f"[Planning] contact search error: {e}")
        return []


def create_appointment(user_id: str, data: dict) -> Optional[dict]:
    sb = get_supabase()
    if not sb:
        return None
    try:
        row = {
            "user_id": user_id,
            "contact_name": data.get("contact_name") or None,
            "title": data.get("title") or "Rendez-vous",
            "description": data.get("description") or "",
            "appointment_date": data.get("appointment_date"),
            "appointment_time": data.get("appointment_time") or "10:00",
            "status": "scheduled",
        }
        # contact_id optionnel
        if data.get("contact_id"):
            row["contact_id"] = data["contact_id"]

        result = sb.table("appointments").insert(row).execute()
        if result.data:
            return result.data[0]
        return row
    except Exception as e:
        print(f"[Planning] insert error: {e}")
        return None


def format_fr_date(date_str: str, time_str: str) -> str:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        weekdays = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        months = ["janvier", "février", "mars", "avril", "mai", "juin",
                  "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
        label = f"{weekdays[d.weekday()]} {d.day} {months[d.month - 1]}"
        if time_str:
            label += f" à {time_str}"
        return label
    except Exception:
        return f"{date_str} {time_str or ''}".strip()


async def run_planning_agent(payload: dict) -> dict:
    instruction = (payload.get("instruction") or "").strip()
    request_id = payload.get("request_id")
    user_id = payload.get("user_id")
    user_name = (payload.get("user_name") or "").strip() or "Anthony"

    if not instruction:
        return {
            "success": False,
            "message": "Aucune instruction reçue.",
            "request_id": request_id
        }

    if not user_id:
        return {
            "success": False,
            "message": "Utilisateur non identifié.",
            "request_id": request_id
        }

    try:
        update_progress(request_id, "contact_found", "Analyse de la demande…")
        intent = await extract_planning_intent(instruction, user_name)

        if not intent.get("has_enough_info") or not intent.get("appointment_date"):
            missing = intent.get("missing") or "la date du rendez-vous"
            return {
                "success": False,
                "message": f"Il me manque {missing}. Peux-tu préciser ?",
                "request_id": request_id
            }

        contact_name = intent.get("contact_name")
        contact_id = None
        if contact_name:
            contacts = search_contacts(contact_name, user_id)
            if len(contacts) == 1:
                contact_id = contacts[0].get("id")
                contact_name = contacts[0].get("full_name") or contact_name
            elif len(contacts) > 1:
                # On prend le premier pour la V1, on pourra faire choose_contact plus tard
                contact_id = contacts[0].get("id")
                contact_name = contacts[0].get("full_name") or contact_name

        appt_data = {
            "contact_name": contact_name,
            "contact_id": contact_id,
            "title": intent.get("title") or "Rendez-vous",
            "description": intent.get("description") or intent.get("raw_summary") or "",
            "appointment_date": intent.get("appointment_date"),
            "appointment_time": intent.get("appointment_time") or "10:00",
        }

        update_progress(request_id, "generating_mail", "Création du rendez-vous…")
        created = create_appointment(user_id, appt_data)

        if not created:
            return {
                "success": False,
                "message": "Impossible d'enregistrer le rendez-vous. Réessaie dans un instant.",
                "request_id": request_id
            }

        when = format_fr_date(appt_data["appointment_date"], appt_data["appointment_time"])
        who = f" avec {contact_name}" if contact_name else ""
        title = "Rendez-vous créé"
        message = f"✅ {appt_data['title']}{who} — {when}."

        update_progress(request_id, "ready", when)

        return {
            "success": True,
            "title": title,
            "message": message,
            "content": message,
            "agent": "planning",
            "appointment": {
                "id": created.get("id") if isinstance(created, dict) else None,
                "date": appt_data["appointment_date"],
                "time": appt_data["appointment_time"],
                "contact_name": contact_name,
                "title": appt_data["title"],
            },
            "request_id": request_id
        }

    except Exception as e:
        print(f"[Planning Agent error] {e}")
        import traceback
        traceback.print_exc()
        update_progress(request_id, "error", str(e))
        return {
            "success": False,
            "message": f"Erreur planning : {str(e)}",
            "request_id": request_id
        }
