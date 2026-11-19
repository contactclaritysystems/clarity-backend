"""
Agent Planning Clarity — Premium (slot-filling)
Ne redemande que l'info manquante. Conserve le reste.
"""

import json
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
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

def _now_paris() -> datetime:
    try:
        return datetime.now(ZoneInfo("Europe/Paris"))
    except Exception:
        return datetime.now()


def bump_weekday_if_past(date_str: str, time_str: Optional[str], instruction: str) -> str:
    """Mardi 9h dit le mardi soir → mardi suivant, jamais une heure déjà passée."""
    if not date_str:
        return date_str
    low = (instruction or "").lower()
    if not re.search(r"\b(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\b", low):
        return date_str
    if re.search(r"\b(aujourd|ce jour)\b", low):
        return date_str
    try:
        hm = (time_str or "00:00")[:5]
        dt = datetime.strptime(f"{str(date_str)[:10]} {hm}", "%Y-%m-%d %H:%M")
        now = _now_paris().replace(tzinfo=None)
        if dt <= now:
            return (dt + timedelta(days=7)).strftime("%Y-%m-%d")
    except Exception:
        pass
    return date_str


def resolve_weekday_and_day(base: datetime, weekday: int, day_num: int) -> Optional[str]:
    """Si 'vendredi 24' : on prend le 24 s'il TOMBE ce jour-là (ce mois ou le suivant).
    Sinon on garde le prochain vendredi — on ne bloque jamais."""
    for month_shift in (0, 1, 2):
        y = base.year
        m = base.month + month_shift
        while m > 12:
            m -= 12
            y += 1
        try:
            d = datetime(y, m, day_num)
        except ValueError:
            continue
        if d.date() < base.date():
            continue
        if d.weekday() == weekday:
            return d.strftime("%Y-%m-%d")
    return None


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
        base = _now_paris().replace(tzinfo=None)
    base = base.replace(hour=0, minute=0, second=0, microsecond=0)
    text = (instruction or "").lower()

    if "après-demain" in text or "apres-demain" in text:
        return (base + timedelta(days=2)).strftime("%Y-%m-%d")
    if re.search(r"\bdemain\b", text):
        return (base + timedelta(days=1)).strftime("%Y-%m-%d")
    if "aujourd" in text:
        return base.strftime("%Y-%m-%d")
    if "fin de journée" in text or "fin de journee" in text or "ce soir" in text:
        return base.strftime("%Y-%m-%d")
    if "fin de matinée" in text or "fin de matinee" in text:
        return base.strftime("%Y-%m-%d")
    if "fin de semaine" in text:
        wd = base.weekday()
        days = (4 - wd) if wd <= 4 else (4 + 7 - wd)
        return (base + timedelta(days=days)).strftime("%Y-%m-%d")

    # 25 août / 25 aout / 25/08 / 25-08-2026
    months = {
        "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
        "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
        "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
    }
    m = re.search(
        r"\b(\d{1,2})\s+(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\b(?:\s+(\d{4}))?",
        text,
    )
    if m:
        day = int(m.group(1))
        month = months[m.group(2)]
        year = int(m.group(3)) if m.group(3) else base.year
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            pass

    m = re.search(r"\b(\d{1,2})[\/\-](\d{1,2})(?:[\/\-](\d{2,4}))?\b", text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else base.year
        if year < 100:
            year += 2000
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            pass

    days_map = {
        "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3,
        "vendredi": 4, "samedi": 5, "dimanche": 6,
    }
    prochain = "prochain" in text or "prochaine" in text
    day_num = None
    mday = re.search(
        r"\b(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+(\d{1,2})\b",
        text,
    )
    if not mday:
        mday = re.search(
            r"\b(\d{1,2})\s+(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\b",
            text,
        )
    if mday:
        n = int(mday.group(1))
        if 1 <= n <= 31:
            day_num = n
    for name, wd in days_map.items():
        if not re.search(rf"\b{name}\b", text):
            continue
        if day_num:
            resolved = resolve_weekday_and_day(base, wd, day_num)
            if resolved:
                return resolved
        return next_weekday(base, wd, prochain=prochain).strftime("%Y-%m-%d")

    m = re.search(r"dans\s+(\d+)\s+jours?", text)
    if m:
        return (base + timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")

    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    return None


def parse_notify_before(text: str) -> Optional[int]:
    """'30 min avant' / '1h avant' / 'une heure avant' → minutes. None = défaut Réglages."""
    t = (text or "").lower()
    if not re.search(r"avant|dans|prévenir|prevenir|rappelle|rapelle|rappel|relanc", t):
        return None
    m = re.search(r"(?:le\s+)?(\d+)\s*(?:min(?:ute)?s?|mn)\s*avant", t)
    if m:
        return max(0, int(m.group(1)))
    m = re.search(r"dans\s+(?:le\s+)?(\d+)\s*(?:min(?:ute)?s?|mn)", t)
    if m:
        return max(0, int(m.group(1)))
    m = re.search(r"(\d+)\s*h(?:eures?)?\s*avant", t)
    if m:
        return max(0, int(m.group(1)) * 60)
    m = re.search(r"dans\s+(\d+)\s*h(?:eures?)?", t)
    if m:
        return max(0, int(m.group(1)) * 60)
    if re.search(r"une\s+heure\s+avant|1\s*h\s*avant", t):
        return 60
    if re.search(r"quart\s+d['’ ]?heure\s+avant|15\s*min", t) and "avant" in t:
        return 15
    if re.search(r"à l['’]?heure|a l['’]?heure|pile", t) and re.search(r"rappel|préven", t):
        return 0
    return None


def parse_french_time(instruction: str) -> Optional[str]:
    raw = (instruction or "").lower()
    if re.search(r"fin de matin[ée]e", raw) and not re.search(r"\d+\s*h", raw):
        return "11:30"
    if ("fin de journée" in raw or "fin de journee" in raw) and not re.search(r"\d+\s*h", raw):
        return "18:00"
    if "fin de semaine" in raw and not re.search(r"\d+\s*h", raw):
        return "17:00"
    if "ce soir" in raw and not re.search(r"\d+\s*h", raw):
        return "18:00"
    text = raw.replace("h", ":")
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


def format_fr_date(date_str: str, time_str: str = "") -> str:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        weekdays = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        # Format humain FR : jeudi 20/08/2026 à 14:00
        label = f"{weekdays[d.weekday()]} {d.strftime('%d/%m/%Y')}"
        if time_str:
            label += f" à {time_str}"
        return label
    except Exception:
        return f"{date_str} {time_str or ''}".strip()


def format_ddmmyyyy(date_str: str) -> str:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return d.strftime("%d/%m/%Y")
    except Exception:
        return date_str or ""


# ---------------------------------------------------------------------------
# Extraction LLM (complète les slots textuels)
# ---------------------------------------------------------------------------

INTENT_SYSTEM = """Tu extrais les infos d'un rendez-vous depuis une dictée française.
Réponds UNIQUEMENT en JSON valide.

Champs :
- contact_name: prénom/nom du client ou null
- title: motif court du RDV ou null (ex: match de foot, devis)
- location: lieu du rendez-vous dicté par l'utilisateur ou null (ex: "à la Timone", "chez le client", "au cabinet")
- description: détails ou ""
- mentioned_weekday: UNIQUEMENT lundi/mardi/mercredi/jeudi/vendredi/samedi/dimanche si l'utilisateur a dit CE mot. Sinon null.
  INTERDIT: demain, après-demain, aujourd'hui, prochain (ce ne sont PAS des weekdays)
- mentioned_day_number: numéro du jour dans le mois ou null (ex: 25)

- Auto-correction en cours de dictée : si l'utilisateur se reprend ("non plutôt", "en fait", "pardon je voulais dire", "euh non") sur une date, heure, nom ou détail, utilise UNIQUEMENT la valeur CORRIGÉE (la dernière dite), jamais l'ancienne. Ex: "vers 9h, non plutôt vers 10h" → retiens 10h, ne mentionne jamais 9h.

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
        for k in ("contact_name", "title", "mentioned_weekday", "location"):
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
    if data.get("notify_minutes_before") is not None:
        try:
            row["notify_minutes_before"] = int(data["notify_minutes_before"])
        except Exception:
            pass
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
            try:
                row.pop("notify_minutes_before", None)
                result = sb.table("appointments").insert(row).execute()
                return (result.data or [row])[0]
            except Exception as e3:
                print(f"[Planning] insert retry2: {e3}")
                return None


def find_conflicting_appointment(user_id: str, date_str: str, time_str: str) -> Optional[dict]:
    """Cherche un RDV deja enregistre a la MEME date+heure pour cet utilisateur
    (hors annules) -- un RDV est trop important pour en ecraser un autre sans
    prevenir l'utilisateur d'abord."""
    sb = get_supabase()
    if not sb or not user_id or not date_str or not time_str:
        return None
    try:
        time_norm = str(time_str)[:5]
        q = (
            sb.table("appointments")
            .select("id, contact_name, title, appointment_date, appointment_time")
            .eq("user_id", user_id)
            .eq("appointment_date", date_str)
            .eq("appointment_time", time_norm)
            .neq("status", "cancelled")
            .limit(1)
            .execute()
        )
        rows = q.data or []
        return rows[0] if rows else None
    except Exception as e:
        print(f"[Planning] find_conflicting_appointment: {e}")
        return None


def update_last_appointment_notify(user_id: str, minutes: int) -> bool:
    sb = get_supabase()
    if not sb or not user_id:
        return False
    try:
        q = (
            sb.table("appointments")
            .select("id")
            .eq("user_id", user_id)
            .neq("status", "cancelled")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = q.data or []
        if not rows:
            q = (
                sb.table("appointments")
                .select("id")
                .eq("user_id", user_id)
                .order("appointment_date", desc=True)
                .limit(1)
                .execute()
            )
            rows = q.data or []
        if not rows:
            return None
        sb.table("appointments").update(
            {"notify_minutes_before": int(minutes)}
        ).eq("id", rows[0]["id"]).eq("user_id", user_id).execute()
        return rows[0]
    except Exception as e:
        print(f"[Planning] update notify: {e}")
        return None


def check_date_consistency(date_str: str, mentioned_weekday: Optional[str]) -> Optional[dict]:
    """Uniquement si un VRAI jour de semaine a été dit et ne correspond pas."""
    if not date_str or not mentioned_weekday:
        return None
    said = mentioned_weekday.lower().strip()
    # Ne jamais traiter demain / aujourd'hui / etc. comme weekday
    if said in ("demain", "après-demain", "apres-demain", "aujourd'hui", "aujourdhui",
                "prochain", "prochaine", "matin", "soir", "midi"):
        return None
    valid = {"lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"}
    if said not in valid:
        return None
    actual = weekday_name_fr(date_str)
    if not actual:
        return None
    if said != actual:
        return {"actual_weekday": actual, "said_weekday": said}
    return None


async def run_planning_agent(payload: dict) -> dict:
    instruction = (payload.get("instruction") or "").strip()
    request_id = payload.get("request_id")
    user_id = payload.get("user_id")
    user_name = (payload.get("user_name") or "").strip() or "Anthony"
    history = payload.get("conversation_history") or ""
    # "pour moi-même" dit explicitement qu'il n'y a pas de contact à trouver :
    # ne pas bloquer le RDV en redemandant un contact/motif dans ce cas.
    is_self_appointment = bool(re.search(
        r"\bpour\s+moi[\s-]?m[êe]me\b", (instruction + " " + (history or "")).lower()
    ))

    # Slots déjà fournis explicitement (suivi)
    slots: Dict[str, Any] = {
        "contact_name": payload.get("contact_name"),
        "title": payload.get("title"),
        "description": payload.get("description") or "",
        "appointment_date": payload.get("appointment_date"),
        "appointment_time": payload.get("appointment_time"),
        "contact_id": payload.get("contact_id"),
        "notify_minutes_before": payload.get("notify_minutes_before"),
    }
    # Nettoyage vides
    slots = {k: v for k, v in slots.items() if v not in (None, "", "null")}
    only_notify = parse_notify_before(instruction)
    looks_only_offset = bool(only_notify is not None and not re.search(
        r"\b(mardi|lundi|mercredi|jeudi|vendredi|samedi|dimanche|demain|aujourd|rdv|rendez)\b",
        (instruction or "").lower(),
    ) and not slots.get("appointment_date"))
    if looks_only_offset and user_id and only_notify is not None:
        patched = update_last_appointment_notify(user_id, int(only_notify))
        if patched:
            nbi = int(only_notify)
            extra = "à l'heure" if nbi <= 0 else (
                f"{nbi} min avant" if nbi < 60 else f"{nbi // 60} h avant"
            )
            appt_id = patched.get("id") if isinstance(patched, dict) else None
            return {
                "success": True,
                "title": "Rappel RDV",
                "message": f"✅ Rappel {extra} pour votre dernier rendez-vous",
                "content": f"✅ Rappel {extra} pour votre dernier rendez-vous",
                "agent": "planning",
                "appointment": {"id": appt_id, "notify_minutes_before": nbi},
                "request_id": request_id,
            }

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
            if llm.get("location"):
                loc = str(llm["location"]).strip()
                existing_desc = (slots.get("description") or "").strip()
                if loc and loc.lower() not in existing_desc.lower():
                    slots["description"] = (
                        (existing_desc + " · " if existing_desc else "") + f"Lieu : {loc}"
                    ).strip()
            nb = parse_notify_before(instruction) or parse_notify_before(history)
            if nb is not None and slots.get("notify_minutes_before") in (None, "", "null"):
                slots["notify_minutes_before"] = nb

            # Jour + quantième incohérents ("vendredi 24" alors que le 24 ne tombe
            # pas un vendredi dans les 3 prochains mois) : parse_french_date privilegie
            # le jour de semaine (il ignore alors silencieusement le "24") -- un RDV
            # est trop important pour laisser passer ça sans le signaler. On recalcule
            # la date que donnerait le quantième seul et on compare : si elle ne tombe
            # pas sur le jour annoncé, on redemande explicitement plutôt que de deviner.
            mentioned_weekday = llm.get("mentioned_weekday")
            mentioned_day_number = llm.get("mentioned_day_number")
            if mentioned_weekday and mentioned_day_number and slots.get("appointment_date"):
                _days_map_check = {
                    "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3,
                    "vendredi": 4, "samedi": 5, "dimanche": 6,
                }
                said_wd = _days_map_check.get(str(mentioned_weekday).lower())
                try:
                    said_day = int(mentioned_day_number)
                except Exception:
                    said_day = None
                if said_wd is not None and said_day and 1 <= said_day <= 31:
                    now_base = _now_paris().replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
                    date_for_daynum = None
                    for month_shift in (0, 1, 2):
                        y, m = now_base.year, now_base.month + month_shift
                        while m > 12:
                            m -= 12
                            y += 1
                        try:
                            d = datetime(y, m, said_day)
                        except ValueError:
                            continue
                        if d.date() < now_base.date():
                            continue
                        date_for_daynum = d
                        break
                    if date_for_daynum and date_for_daynum.weekday() != said_wd:
                        chosen_date = slots["appointment_date"]
                        daynum_date_str = date_for_daynum.strftime("%Y-%m-%d")
                        actual_wd_name = weekday_name_fr(daynum_date_str)
                        if chosen_date != daynum_date_str:
                            return {
                                "success": False,
                                "reason": "date_ambiguous",
                                "message": (
                                    f"Attention : le {format_ddmmyyyy(daynum_date_str)} tombe un "
                                    f"{actual_wd_name}, pas un {mentioned_weekday}. Quelle date voulez-vous ?"
                                ),
                                "suggestions": [
                                    {
                                        "label": f"{str(mentioned_weekday).capitalize()} {format_ddmmyyyy(chosen_date)}",
                                        "date": chosen_date,
                                    },
                                    {
                                        "label": f"{actual_wd_name.capitalize()} {format_ddmmyyyy(daynum_date_str)}",
                                        "date": daynum_date_str,
                                    },
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

        if slots.get("appointment_date") and slots.get("appointment_time"):
            slots["appointment_date"] = bump_weekday_if_past(
                slots["appointment_date"],
                slots["appointment_time"],
                instruction + " " + (history or ""),
            )

        if not slots.get("appointment_time"):
            return {
                "success": False,
                "reason": "missing_time",
                "message": retained_msg("À quelle heure ?"),
                "partial": slots,
                "context_hint": f"RDV partial: {partial_tag}",
                "request_id": request_id,
            }

        title = (slots.get("title") or "").strip()
        if title.lower() in ("rendez-vous", "rdv", "rendez vous"):
            title = ""
        contact = (slots.get("contact_name") or "").strip()
        if not contact and not title and not is_self_appointment:
            return {
                "success": False,
                "reason": "missing_contact",
                "message": retained_msg("Avec qui, ou pour quel motif ?"),
                "partial": slots,
                "context_hint": f"RDV partial: {partial_tag}",
                "request_id": request_id,
            }
        if not slots.get("title"):
            slots["title"] = title or "Rendez-vous"

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

        # Conflit d'agenda : un RDV existe deja a la MEME date+heure pour cet
        # utilisateur -- on previent avant d'ecraser silencieusement (cf.
        # Anthony : "Un rdv est important il ne dois pas y avoir d'erreur de
        # la part de Clarity"). confirm_conflict (envoye par le frontend sur
        # "Enregistrer quand meme") permet de passer outre ce garde-fou une
        # fois l'utilisateur prevenu.
        if (
            not payload.get("confirm_conflict")
            and slots.get("appointment_date")
            and slots.get("appointment_time")
        ):
            conflict = find_conflicting_appointment(
                user_id, slots["appointment_date"], slots["appointment_time"]
            )
            if conflict:
                conflict_who = conflict.get("contact_name") or conflict.get("title") or "un rendez-vous"
                when_txt = format_fr_date(slots["appointment_date"], slots["appointment_time"])
                return {
                    "success": False,
                    "reason": "schedule_conflict",
                    "message": (
                        f"Attention : vous avez déjà {conflict_who} le {when_txt}. "
                        f"Voulez-vous quand même enregistrer ce rendez-vous, ou changer le jour/l'heure ?"
                    ),
                    "conflict": conflict,
                    "partial": slots,
                    "request_id": request_id,
                }

        nb_final = slots.get("notify_minutes_before")
        if nb_final is None:
            nb_final = parse_notify_before(instruction) or parse_notify_before(history)
        created = create_appointment(user_id, {
            **slots,
            "contact_name": contact_name,
            "contact_id": contact_id,
            "notify_minutes_before": nb_final,
        })
        if created and nb_final is not None and created.get("id"):
            try:
                sb = get_supabase()
                if sb:
                    sb.table("appointments").update(
                        {"notify_minutes_before": int(nb_final)}
                    ).eq("id", created["id"]).eq("user_id", user_id).execute()
                    slots["notify_minutes_before"] = int(nb_final)
            except Exception as e:
                print(f"[Planning] notify after insert: {e}")

        if not created:
            return {
                "success": False,
                "message": "Impossible d'enregistrer le rendez-vous.",
                "request_id": request_id,
            }

        when = format_fr_date(slots["appointment_date"], slots["appointment_time"])
        who = f" avec {contact_name}" if contact_name else ""
        extra = ""
        nb = slots.get("notify_minutes_before")
        if nb is not None:
            try:
                nbi = int(nb)
                extra = " · rappel à l'heure" if nbi <= 0 else (
                    f" · rappel {nbi} min avant" if nbi < 60 else f" · rappel {nbi // 60} h avant"
                )
            except Exception:
                extra = ""
        message = f"✅ RDV noté{who} — {when}{extra}"

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
                "notify_minutes_before": slots.get("notify_minutes_before"),
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
