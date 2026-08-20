"""
Agent Assistant Clarity — natif Render
Répond avec le contexte réel de l'utilisateur (planning, relances, profil).
"""

import os
from datetime import datetime, timedelta
from typing import Optional, Any, Dict, List
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


def load_user_context(user_id: Optional[str], user_name: str = "") -> str:
    """Charge un résumé court et utile (pas tout l'historique)."""
    if not user_id:
        return f"Utilisateur : {user_name or 'inconnu'} (pas d'user_id → pas de données planning)."

    sb = get_supabase()
    if not sb:
        return f"Utilisateur : {user_name or user_id}. Supabase indisponible."

    lines: List[str] = [f"Utilisateur : {user_name or user_id}"]
    today = datetime.now().strftime("%Y-%m-%d")
    in_14 = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")

    # RDV à venir
    try:
        appts = (
            sb.table("appointments")
            .select("title, appointment_date, appointment_time, contact_name, status, description")
            .eq("user_id", user_id)
            .gte("appointment_date", today)
            .lte("appointment_date", in_14)
            .order("appointment_date")
            .limit(15)
            .execute()
        )
        rows = appts.data or []
        if rows:
            lines.append("Rendez-vous (14 prochains jours) :")
            for a in rows:
                d = a.get("appointment_date") or "?"
                t = a.get("appointment_time") or ""
                title = a.get("title") or "RDV"
                contact = a.get("contact_name") or ""
                st = (a.get("status") or "").lower()
                status_fr = {
                    "done": "terminé",
                    "completed": "terminé",
                    "scheduled": "à venir",
                    "pending": "à venir",
                    "cancelled": "annulé",
                    "canceled": "annulé",
                }.get(st, "")
                bit = f"- {d}"
                if t:
                    bit += f" à {t}"
                bit += f" — {title}"
                if contact:
                    bit += f" avec {contact}"
                if status_fr:
                    bit += f" ({status_fr})"
                lines.append(bit)
        else:
            lines.append("Rendez-vous (14 j) : aucun.")
    except Exception as e:
        lines.append(f"Rendez-vous : erreur lecture ({e})")

    # Relances / rappels pending
    try:
        fus = (
            sb.table("follow_ups")
            .select("reason, reminder_date, reminder_time, contact_name, status, message_context")
            .eq("user_id", user_id)
            .eq("status", "pending")
            .order("reminder_date")
            .limit(15)
            .execute()
        )
        rows = fus.data or []
        if rows:
            lines.append("Rappels / relances en attente :")
            for f in rows:
                d = f.get("reminder_date") or "?"
                t = f.get("reminder_time") or ""
                reason = f.get("reason") or "Rappel"
                contact = f.get("contact_name") or ""
                bit = f"- {d}"
                if t:
                    bit += f" {t}"
                bit += f" — {reason}"
                if contact and contact != "Moi":
                    bit += f" ({contact})"
                lines.append(bit)
        else:
            lines.append("Rappels / relances en attente : aucun.")
    except Exception as e:
        lines.append(f"Rappels : erreur lecture ({e})")

    # Quelques contacts (pour questions du type "j'ai le contact de…")
    try:
        contacts = (
            sb.table("contacts")
            .select("full_name, email, company")
            .eq("user_id", user_id)
            .order("full_name")
            .limit(30)
            .execute()
        )
        rows = contacts.data or []
        if rows:
            lines.append(f"Contacts enregistrés ({len(rows)} affichés, max 30) :")
            for c in rows[:20]:
                name = c.get("full_name") or "?"
                email = c.get("email") or ""
                company = c.get("company") or ""
                bit = f"- {name}"
                if company:
                    bit += f" ({company})"
                if email:
                    bit += f" — {email}"
                lines.append(bit)
        else:
            lines.append("Contacts : aucun.")
    except Exception as e:
        lines.append(f"Contacts : erreur lecture ({e})")

    return "\n".join(lines)


SYSTEM = """Tu es Clarity, l'assistante pro de l'utilisateur (artisan / dirigeant TPE-PME).
Tu lui parles DIRECTEMENT en français, tutoiement naturel (tu / ton / ta).
Jamais "l'utilisateur", jamais "vous avez" à la 3e personne, jamais "le client du compte".

RÈGLES :
1. Priorité au CONTEXTE fourni (RDV, rappels, contacts).
2. Si l'info est dans le contexte → réponds avec les faits (dates jj/mm, heures, noms).
3. N'affiche JAMAIS les codes techniques de statut (done, scheduled, pending, cancelled…).
   Traduis si besoin : terminé, à venir, en attente.
4. N'invente rien. Si tu ne sais pas → dis-le simplement.
5. Réponses courtes et claires.
6. Tu n'exécutes pas les actions (mail, RDV) : tu informes seulement.
"""


async def run_assistant_agent(payload: dict) -> dict:
    instruction = (payload.get("instruction") or "").strip()
    request_id = payload.get("request_id")
    user_id = payload.get("user_id")
    user_name = (payload.get("user_name") or "").strip()
    history = payload.get("conversation_history") or ""

    if not instruction:
        return {
            "success": False,
            "message": "Quelle est ta question ?",
            "request_id": request_id,
        }

    try:
        context = load_user_context(user_id, user_name)
        today_fr = datetime.now().strftime("%d/%m/%Y %H:%M")

        user_msg = (
            f"Date/heure actuelle : {today_fr}\n\n"
            f"=== CONTEXTE CLARITY (données réelles) ===\n{context}\n\n"
        )
        if history:
            user_msg += f"=== HISTORIQUE RÉCENT ===\n{history}\n\n"
        user_msg += f"=== QUESTION ===\n{instruction}"

        response = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=900,
        )
        answer = (response.choices[0].message.content or "").strip()
        if not answer:
            answer = "Je n'ai pas pu formuler de réponse. Reformule ta question."

        return {
            "success": True,
            "title": "Clarity",
            "message": answer,
            "content": answer,
            "request_id": request_id,
        }
    except Exception as e:
        print(f"[Assistant] error: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "message": f"Erreur assistant : {e}",
            "request_id": request_id,
        }
