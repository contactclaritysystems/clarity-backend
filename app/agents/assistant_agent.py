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


SYSTEM = """Tu es Clarity, l'assistante professionnelle d'un dirigeant / artisan (TPE-PME).
Tu vous adressez TOUJOURS à lui avec le VOUVOIEMENT (vous / votre / vos).
Jamais de tutoiement. Jamais "l'utilisateur". Jamais la 3e personne.

RÈGLES :
1. Priorité au CONTEXTE CLARITY (RDV, rappels, contacts) pour tout ce qui concerne SON activité.
2. Si une section "RECHERCHE WEB" est fournie → utilise-la pour les faits d'actualité / chiffres / infos externes.
3. N'affiche JAMAIS les codes techniques (done, scheduled, pending…).
   Dis plutôt : terminé, à venir, en attente.
4. N'invente pas de RDV, contacts ou horaires absents du contexte.
5. Réponses claires, structurées, professionnelles, pas trop longues.
6. Vous n'exécutez pas les actions (mail, RDV) : vous informez seulement.
"""



def needs_web_search(instruction: str) -> bool:
    """Heuristique : questions d'actu / faits externes."""
    t = (instruction or "").lower()
    keys = [
        "bitcoin", "crypto", "bourse", "cours de", "prix de", "météo", "meteo",
        "actualité", "actualite", "news", "aujourd'hui en france", "qui a gagné",
        "résultat du match", "resultat du match", "élection", "election",
        "inflation", "taux", "dollar", "euro", "nasdaq", "cac 40",
        "dernières infos", "dernieres infos", "temps réel", "temps reel",
        "google", "wikipedia", "c'est quoi", "qui est", "quand a eu lieu",
    ]
    # Questions purement perso Clarity → pas de web
    perso = ["rendez-vous", "rdv", "rappel", "relance", "contact", "mon planning",
             "ma journée", "mes rendez-vous", "ai-je", "j'ai comme"]
    if any(p in t for p in perso) and not any(k in t for k in keys):
        return False
    if any(k in t for k in keys):
        return True
    # Formulations ouvertes d'info générale
    if t.startswith(("c'est quoi", "que s'est-il", "quelle est la", "quel est le", "combien vaut")):
        return True
    return False


def web_search(query: str, max_results: int = 5) -> str:
    """Recherche web légère (DuckDuckGo) — sans clé API."""
    try:
        import json
        import urllib.parse
        import urllib.request
        q = urllib.parse.quote(query)
        url = f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1"
        req = urllib.request.Request(url, headers={"User-Agent": "ClarityAssistant/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        parts = []
        if data.get("AbstractText"):
            parts.append(data["AbstractText"])
            if data.get("AbstractURL"):
                parts.append(f"Source: {data['AbstractURL']}")
        for t in (data.get("RelatedTopics") or [])[:max_results]:
            if isinstance(t, dict) and t.get("Text"):
                parts.append(t["Text"])
            elif isinstance(t, dict) and "Topics" in t:
                for sub in t["Topics"][:2]:
                    if sub.get("Text"):
                        parts.append(sub["Text"])
        # Fallback HTML lite si vide
        if not parts:
            url2 = f"https://html.duckduckgo.com/html/?q={q}"
            req2 = urllib.request.Request(url2, headers={"User-Agent": "Mozilla/5.0 ClarityBot"})
            with urllib.request.urlopen(req2, timeout=8) as resp2:
                html = resp2.read().decode("utf-8", errors="ignore")
            import re
            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</', html, re.I | re.S)
            for s in snippets[:max_results]:
                clean = re.sub(r"<[^>]+>", "", s).strip()
                if clean:
                    parts.append(clean)
        if not parts:
            return ""
        return "\n".join(f"- {p}" for p in parts[:max_results])
    except Exception as e:
        print(f"[Assistant web_search] {e}")
        return ""


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

        web_block = ""
        if needs_web_search(instruction):
            raw = web_search(instruction)
            if raw:
                web_block = f"=== RECHERCHE WEB (faits récupérés maintenant) ===\n{raw}\n\n"
            else:
                web_block = (
                    "=== RECHERCHE WEB ===\n"
                    "Aucun résultat fiable récupéré. Indiquez-le poliment et "
                    "proposez de reformuler ou de vérifier sur un site officiel.\n\n"
                )

        user_msg = (
            f"Date/heure actuelle : {today_fr}\n\n"
            f"=== CONTEXTE CLARITY (données réelles) ===\n{context}\n\n"
        )
        if web_block:
            user_msg += web_block
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
