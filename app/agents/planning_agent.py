"""
Agent Planning Clarity — Premium (slot-filling)
Ne redemande que l'info manquante. Conserve le reste.
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


# ---------------------------------------------------------------------------
# Dates FR déterministes
# ---------------------------------------------------------------------------

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

    # Date ISO déjà fournie
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    return None


def parse_french_time(instruction: str) -> Optional[str]:
    text = (instruction or "").lower().replace("h", ":")
    # 14:00 / 14:30
    m = re.search(r"\b([01]?\d|2[0-3])[:\.]([0-5]\d)\b", text)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    # 14h / 9h
    m = re.search(r"\b([01]?\d|2[0-3])\s*h\b", (instruction or "").lower())
    if m:
        return f"{int(m.group(1)):02d}:00"
    # "14 heures"
    m = re.search(r"\b([01]?\d|2[0-3])\s*heures?\b", (instruction or "").lower())
    if m:
        return f"{int(m.group(1)):02d}:00"
    return None


def weekday_name_fr(date_str: str) -> str:
    names = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return names[d.weekday()]
    except Exception:
        return ""


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


# ---------------------------------------------------------------------------
# Extraction LLM (complète les slots textuels)
# ---------------------------------------------------------------------------

INTENT_SYSTEM = """Tu extrais les infos d'un rendez-vous depuis une dictée française.
Réponds UNIQUEMENT en JSON valide.

Champs :
- contact_name: prénom/nom du client ou null
- title: motif court ou null
- description: détails ou ""
- mentioned_weekday: lundi/mardi/.../null (si l'utilisateur a dit un jour)
- mentioned_day_number: numéro du jour dans le mois ou null (ex: 25)

Ne calcule PAS la date finale. Juste ce qui est dit.
"""


async def extract_slots_llm(instruction: str) -> dict:
    try:
        response = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": INTENT_SYSTEM},
                {"role": "user", "content": instruction}
            ],
            temperature=0.1,
            max_tokens=300,
            response_format={"type": "json_object"}
        )
        raw = (response.choices[0].message.content or "{}").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:].strip()
        data = json.loads(raw)
        for k in ("contact_name", "title", "mentioned_weekday"):
            if data.get(k) in ("null", "None", ""):
                data[k] = None
        return data
    except Exception as e:
        print(f"[Planning LLM] {e}")
        return {}


def merge_from_history(history: str) -> dict:
    """Récupère les slots déjà connus dans l'historique de conversation."""
    slots = {}
    if not history:
        return slots
    m = re.search(r"date[=:]\s*(\d{4}-\d{2}-\d{2})", history, re.I)
    if m:
        slots["appointment_date"] = m.group(1)
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", history)
    if m and "appointment_date" not in slots:
        slots["appointment_date"] = m.group(1)
    m = re.search(r"heure[=:]\s*(\d{2}:\d{2})", history, re.I)
    if m:
        slots["appointment_time"] = m.group(1)
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", history)
    if m and "appointment_time" not in slots:
        slots["appointment_time"] = f"{int(m.group(1)):02d}:{m.group(2)}"
    m = re.search(r"contact[=:]\s*([^\n|;]+)", history, re.I)
    if m:
        slots["contact_name"] = m.group(1).strip()
    m = re.search(r"motif[=:]\s*([^\n|;]+)", history, re.I)
    if m:
        slots["title"] = m.group(1).strip()
    m = re.search(r"RDV partial:\s*(\{.*?\})", history)
    if m:
        try:
            slots.update(json.loads(m.group(1)))
        except Exception:
            pass
    return slots


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
        print(f"[Planning] contact search: {e}")
        return []


def create_appointment(user_id: str, data: dict) -> Optional[dict]:
    sb = get_supabase()
    if not sb:
        return None
    row = {
        "user_id": user_id,
        "contact_name": data.get("contact_name") or None,
        "title": data.get("title") or "Rendez-vous",
        "description": data.get("description") or "",
        "appointment_date": data.get("appointment_date"),
        "appointment_time": (data.get("appointment_time") or "10:00")[:5],
        "status": "scheduled",
    }
    try:
        if data.get("contact_id"):
            row["contact_id"] = data["contact_id"]
        result = sb.table("appointments").insert(row).execute()
        return (result.data or [row])[0]
    except Exception as e:
        print(f"[Planning] insert: {e}")
        try:
            row.pop("contact_id", None)
            result = sb.table("appointments").insert(row).execute()
            return (result.data or [row])[0]
        except Exception as e2:
            print(f"[Planning] insert retry: {e2}")
            return None


def check_date_consistency(date_str: str, mentioned_weekday: Optional[str]) -> Optional[dict]:
    """Si l'utilisateur a dit un jour qui ne correspond pas à la date → suggestions."""
    if not date_str or not mentioned_weekday:
        return None
    actual = weekday_name_fr(date_str)
    if not actual:
        return None
    if mentioned_weekday.lower().strip() != actual:
        # Propose la date qui match le jour dit, et celle du numéro
        return {
            "actual_weekday": actual,
            "said_weekday": mentioned_weekday.lower().strip(),
        }
    return None


async def run_planning_agent(payload: dict) -> dict:
    instruction = (payload.get("instruction") or "").strip()
    request_id = payload.get("request_id")
    user_id = payload.get("user_id")
    user_name = (payload.get("user_name") or "").strip() or "Anthony"
    history = payload.get("conversation_history") or ""

    # Slots déjà fournis explicitement (suivi)
    slots: Dict[str, Any] = {
        "contact_name": payload.get("contact_name"),
        "title": payload.get("title"),
        "description": payload.get("description") or "",
        "appointment_date": payload.get("appointment_date"),
        "appointment_time": payload.get("appointment_time"),
        "contact_id": payload.get("contact_id"),
    }
    # Nettoyage vides
    slots = {k: v for k, v in slots.items() if v not in (None, "", "null")}

    # Historique
    slots.update({k: v for k, v in merge_from_history(history).items() if k not in slots})

    if not instruction and not slots.get("appointment_date"):
        return {
            "success": False,
            "reason": "missing_date",
            "message": "Pour quel jour est le rendez-vous ?",
            "partial": slots,
            "request_id": request_id,
        }

    try:
        # Extraction : dictée courante + historique (pour ne pas perdre date/heure déjà dites)
        combined = f"{history}\n{instruction}".strip()
        if combined:
            fixed_date = parse_french_date(instruction) or parse_french_date(history)
            fixed_time = parse_french_time(instruction) or parse_french_time(history)
            if fixed_date and not slots.get("appointment_date"):
                slots["appointment_date"] = fixed_date
            elif fixed_date and instruction and parse_french_date(instruction):
                slots["appointment_date"] = fixed_date  # la nouvelle dictée prime
            if fixed_time and not slots.get("appointment_time"):
                slots["appointment_time"] = fixed_time
            elif fixed_time and instruction and parse_french_time(instruction):
                slots["appointment_time"] = fixed_time

            llm = await extract_slots_llm(instruction)
            if llm.get("contact_name") and not slots.get("contact_name"):
                slots["contact_name"] = llm["contact_name"]
            if llm.get("title") and not slots.get("title"):
                slots["title"] = llm["title"]
            if llm.get("description"):
                slots["description"] = llm["description"]

            # Cohérence jour / date
            inconsistency = check_date_consistency(
                slots.get("appointment_date"),
                llm.get("mentioned_weekday")
            )
            if inconsistency and slots.get("appointment_date"):
                said = inconsistency["said_weekday"]
                actual = inconsistency["actual_weekday"]
                # Calculer la date du jour dit (prochain)
                days_map = {
                    "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3,
                    "vendredi": 4, "samedi": 5, "dimanche": 6,
                }
                alt = None
                if said in days_map:
                    alt = next_weekday(datetime.now(), days_map[said], prochain=True).strftime("%Y-%m-%d")
                return {
                    "success": False,
                    "reason": "date_ambiguous",
                    "message": f"Attention : le {slots['appointment_date']} tombe un {actual}, pas un {said}.",
                    "suggestions": [
                        {"label": f"{actual.capitalize()} {slots['appointment_date']}", "date": slots["appointment_date"]},
                        *([{"label": f"{said.capitalize()} {alt}", "date": alt}] if alt else []),
                    ],
                    "partial": {**slots, "appointment_date": None},
                    "request_id": request_id,
                }

        # --- Slot filling : ne demander que le trou ---
        partial_tag = json.dumps({
            k: slots.get(k) for k in (
                "contact_name", "title", "appointment_date", "appointment_time", "description"
            ) if slots.get(k)
        }, ensure_ascii=False)

        def retained_msg(question: str) -> str:
            """Question claire + rappel humain des infos déjà connues (sans format technique)."""
            bits = []
            if slots.get("contact_name"):
                bits.append(slots["contact_name"])
            if slots.get("appointment_date"):
                when = format_fr_date(
                    slots["appointment_date"],
                    slots.get("appointment_time") or ""
                )
                bits.append(when)
            elif slots.get("appointment_time"):
                bits.append(f"à {slots['appointment_time']}")
            if slots.get("title") and slots.get("title") != "Rendez-vous":
                bits.append(f"« {slots['title']} »")
            if bits:
                return f"{question}\n({', '.join(bits)})"
            return question

        if not slots.get("appointment_date"):
            return {
                "success": False,
                "reason": "missing_date",
                "message": retained_msg("Pour quel jour ? (ex: demain, mardi prochain…)"),
                "partial": slots,
                "context_hint": f"RDV partial: {partial_tag}",
                "request_id": request_id,
            }

        if not slots.get("appointment_time"):
            return {
                "success": False,
                "reason": "missing_time",
                "message": retained_msg("À quelle heure ?"),
                "partial": slots,
                "context_hint": f"RDV partial: {partial_tag}",
                "request_id": request_id,
            }

        if not slots.get("contact_name"):
            return {
                "success": False,
                "reason": "missing_contact",
                "message": retained_msg("Avec qui est le rendez-vous ?"),
                "partial": slots,
                "context_hint": f"RDV partial: {partial_tag}",
                "request_id": request_id,
            }

        # Motif : défaut soft, on ne bloque pas
        if not slots.get("title"):
            slots["title"] = "Rendez-vous"

        # Résoudre contact
        contact_id = slots.get("contact_id")
        contact_name = slots.get("contact_name")
        if contact_name and user_id and not contact_id:
            found = search_contacts(contact_name, user_id)
            if len(found) == 1:
                contact_id = found[0].get("id")
                contact_name = found[0].get("full_name") or contact_name
            elif len(found) > 1:
                return {
                    "success": False,
                    "reason": "choose_contact",
                    "action": "choose_contact",
                    "message": "Plusieurs contacts trouvés. Lequel ?",
                    "contacts": found,
                    "partial": slots,
                    "request_id": request_id,
                }

        if not user_id:
            return {
                "success": False,
                "message": "Utilisateur non identifié.",
                "request_id": request_id,
            }

        created = create_appointment(user_id, {
            **slots,
            "contact_name": contact_name,
            "contact_id": contact_id,
        })

        if not created:
            return {
                "success": False,
                "message": "Impossible d'enregistrer le rendez-vous.",
                "request_id": request_id,
            }

        when = format_fr_date(slots["appointment_date"], slots["appointment_time"])
        who = f" avec {contact_name}" if contact_name else ""
        message = f"✅ RDV noté{who} — {when}"

        update_progress(request_id, "ready", when)

        return {
            "success": True,
            "title": "Rendez-vous créé",
            "message": message,
            "content": message,
            "agent": "planning",
            "appointment": {
                "id": created.get("id") if isinstance(created, dict) else None,
                "date": slots["appointment_date"],
                "time": slots["appointment_time"],
                "contact_name": contact_name,
                "title": slots.get("title"),
            },
            "request_id": request_id,
        }

    except Exception as e:
        print(f"[Planning Agent error] {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "message": f"Erreur planning : {str(e)}",
            "request_id": request_id,
        }
