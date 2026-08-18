"""
Agent Relances Clarity — Premium (slot-filling)
Rappels personnels + relances clients. Ne redemande que le trou.
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


def next_weekday(base: datetime, target_weekday: int, prochain: bool = False) -> datetime:
    current = base.weekday()
    days_ahead = target_weekday - current
    if days_ahead < 0:
        days_ahead += 7
    elif days_ahead == 0:
        days_ahead = 7 if prochain else 0
    elif prochain and days_ahead > 0:
        days_ahead += 7
    return base + timedelta(days=days_ahead)


def parse_french_date(instruction: str, base: Optional[datetime] = None) -> Optional[str]:
    if base is None:
        base = datetime.now()
    base = base.replace(hour=0, minute=0, second=0, microsecond=0)
    text = (instruction or "").lower()

    if "après-demain" in text or "apres-demain" in text:
        return (base + timedelta(days=2)).strftime("%Y-%m-%d")
    if re.search(r"\bdemain\b", text):
        return (base + timedelta(days=1)).strftime("%Y-%m-%d")
    if "aujourd" in text:
        return base.strftime("%Y-%m-%d")
    if "ce soir" in text:
        return base.strftime("%Y-%m-%d")

    days_map = {
        "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3,
        "vendredi": 4, "samedi": 5, "dimanche": 6,
    }
    prochain = "prochain" in text or "prochaine" in text
    for name, wd in days_map.items():
        if name in text:
            return next_weekday(base, wd, prochain=prochain).strftime("%Y-%m-%d")

    m = re.search(r"dans\s+(\d+)\s+jours?", text)
    if m:
        return (base + timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def parse_french_time(instruction: str) -> Optional[str]:
    text = (instruction or "").lower()
    if "ce soir" in text and not re.search(r"\d", text):
        return "18:00"
    if "matin" in text and not re.search(r"\d+\s*h", text):
        return "08:00"
    if "midi" in text:
        return "12:00"

    m = re.search(r"\b([01]?\d|2[0-3])[:\.]([0-5]\d)\b", text.replace("h", ":"))
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    m = re.search(r"\b([01]?\d|2[0-3])\s*h\b", text)
    if m:
        return f"{int(m.group(1)):02d}:00"
    m = re.search(r"\b([01]?\d|2[0-3])\s*heures?\b", text)
    if m:
        return f"{int(m.group(1)):02d}:00"
    return None


def format_fr_date(date_str: str, time_str: str = "") -> str:
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


INTENT_SYSTEM = """Tu extrais un rappel / une relance depuis une dictée française.
Réponds UNIQUEMENT en JSON valide.

{
  "reason": "phrase courte du rappel (ex: ne pas oublier les outils)",
  "contact_name": "nom du client si relance client, sinon null",
  "message_context": "détails éventuels ou null"
}

reason = ce qu'il ne faut pas oublier, formulé clairement, sans 'rappelle-moi'.
"""


async def extract_llm(instruction: str) -> dict:
    try:
        response = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": INTENT_SYSTEM},
                {"role": "user", "content": instruction}
            ],
            temperature=0.1,
            max_tokens=250,
            response_format={"type": "json_object"}
        )
        raw = (response.choices[0].message.content or "{}").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:].strip()
        data = json.loads(raw)
        for k in list(data.keys()):
            if data.get(k) in ("null", "None", ""):
                data[k] = None
        return data
    except Exception as e:
        print(f"[Relance LLM] {e}")
        return {}


def merge_from_history(history: str) -> dict:
    slots = {}
    if not history:
        return slots
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", history)
    if m:
        slots["reminder_date"] = m.group(1)
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", history)
    if m:
        slots["reminder_time"] = f"{int(m.group(1)):02d}:{m.group(2)}"
    m = re.search(r"date[=:]\s*(\d{4}-\d{2}-\d{2})", history, re.I)
    if m:
        slots["reminder_date"] = m.group(1)
    m = re.search(r"heure[=:]\s*(\d{2}:\d{2})", history, re.I)
    if m:
        slots["reminder_time"] = m.group(1)
    m = re.search(r"motif[=:]\s*([^\n]+)", history, re.I)
    if m:
        slots["reason"] = m.group(1).strip()
    return slots


def create_follow_up(user_id: str, data: dict) -> Optional[dict]:
    sb = get_supabase()
    if not sb:
        return None

    note = data.get("message_context") or ""
    time_str = data.get("reminder_time")
    if time_str:
        # Conserve l'heure dans la note si pas de colonne dédiée
        prefix = f"⏰ {time_str}\n"
        if time_str not in (note or ""):
            note = prefix + (note or "")

    row = {
        "user_id": user_id,
        "contact_name": data.get("contact_name") or None,
        "reason": data.get("reason") or "Rappel",
        "message_context": note,
        "reminder_date": data.get("reminder_date"),
        "status": "pending",
    }
    try:
        # tentative avec reminder_time si la colonne existe
        row_with_time = dict(row)
        if time_str:
            row_with_time["reminder_time"] = time_str
        result = sb.table("follow_ups").insert(row_with_time).execute()
        return (result.data or [row_with_time])[0]
    except Exception as e:
        print(f"[Relance] insert with time failed: {e}")
        try:
            result = sb.table("follow_ups").insert(row).execute()
            return (result.data or [row])[0]
        except Exception as e2:
            print(f"[Relance] insert error: {e2}")
            return None


async def run_relance_agent(payload: dict) -> dict:
    instruction = (payload.get("instruction") or "").strip()
    request_id = payload.get("request_id")
    user_id = payload.get("user_id")
    history = payload.get("conversation_history") or ""

    slots: Dict[str, Any] = {
        "reason": payload.get("reason"),
        "contact_name": payload.get("contact_name"),
        "message_context": payload.get("message_context"),
        "reminder_date": payload.get("reminder_date"),
        "reminder_time": payload.get("reminder_time"),
    }
    slots = {k: v for k, v in slots.items() if v not in (None, "", "null")}
    slots.update({k: v for k, v in merge_from_history(history).items() if k not in slots})

    if not instruction and not slots.get("reason"):
        return {
            "success": False,
            "reason": "missing_content",
            "message": "De quoi dois-je te rappeler ?",
            "request_id": request_id,
        }

    try:
        # Dates / heures depuis dictée + historique
        d_inst = parse_french_date(instruction) if instruction else None
        d_hist = parse_french_date(history) if history else None
        t_inst = parse_french_time(instruction) if instruction else None
        t_hist = parse_french_time(history) if history else None

        if d_inst:
            slots["reminder_date"] = d_inst
        elif d_hist and not slots.get("reminder_date"):
            slots["reminder_date"] = d_hist

        if t_inst:
            slots["reminder_time"] = t_inst
        elif t_hist and not slots.get("reminder_time"):
            slots["reminder_time"] = t_hist

        # Si "fais-moi penser" sans date → demain par défaut (on demandera l'heure)
        personal_reminder = bool(re.search(
            r"fais[- ]moi penser|rappelle[- ]moi|n'oublie pas|ne pas oublier|pense à|pense a",
            (instruction or "").lower()
        ))

        if instruction:
            llm = await extract_llm(instruction)
            if llm.get("reason") and not slots.get("reason"):
                slots["reason"] = llm["reason"]
            if llm.get("contact_name") and not slots.get("contact_name"):
                slots["contact_name"] = llm["contact_name"]
            if llm.get("message_context") and not slots.get("message_context"):
                slots["message_context"] = llm["message_context"]

        # Si rappel perso sans date → demain
        if personal_reminder and not slots.get("reminder_date"):
            slots["reminder_date"] = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        def retained_msg(question: str) -> str:
            bits = []
            if slots.get("reason"):
                bits.append(slots["reason"])
            if slots.get("reminder_date"):
                bits.append(format_fr_date(slots["reminder_date"], slots.get("reminder_time") or ""))
            elif slots.get("reminder_time"):
                bits.append(f"à {slots['reminder_time']}")
            if bits:
                return f"{question}\n({', '.join(bits)})"
            return question

        # Contenu du rappel obligatoire
        if not slots.get("reason"):
            return {
                "success": False,
                "reason": "missing_content",
                "message": retained_msg("De quoi dois-je te rappeler ?"),
                "partial": slots,
                "request_id": request_id,
            }

        if not slots.get("reminder_date"):
            return {
                "success": False,
                "reason": "missing_date",
                "message": retained_msg("Pour quel jour ?"),
                "partial": slots,
                "request_id": request_id,
            }

        if not slots.get("reminder_time"):
            return {
                "success": False,
                "reason": "missing_time",
                "message": retained_msg("À quelle heure veux-tu que je te le rappelle ?"),
                "partial": slots,
                "request_id": request_id,
            }

        if not user_id:
            return {
                "success": False,
                "message": "Utilisateur non identifié.",
                "request_id": request_id,
            }

        created = create_follow_up(user_id, slots)
        if not created:
            return {
                "success": False,
                "message": "Impossible d'enregistrer le rappel.",
                "request_id": request_id,
            }

        when = format_fr_date(slots["reminder_date"], slots.get("reminder_time") or "")
        message = f"✅ Rappel noté — {slots['reason']} ({when})"

        return {
            "success": True,
            "title": "Rappel créé",
            "message": message,
            "content": message,
            "agent": "relance",
            "follow_up": {
                "id": created.get("id") if isinstance(created, dict) else None,
                "date": slots["reminder_date"],
                "time": slots.get("reminder_time"),
                "reason": slots.get("reason"),
            },
            "request_id": request_id,
        }

    except Exception as e:
        print(f"[Relance error] {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "message": f"Erreur relance : {str(e)}",
            "request_id": request_id,
        }
