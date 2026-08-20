"""
Agent Rédaction Clarity — natif Render
Textes pro : LinkedIn, comptes-rendus, notes, messages, annonces…
"""

import os
from datetime import datetime
from typing import Optional
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")


def get_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY manquante")
    return OpenAI(api_key=api_key)


SYSTEM = """Tu es le module de rédaction de Clarity Systems, SaaS français ultra-premium
pour dirigeants et artisans (TPE-PME).

Tu rédiges des textes prêts à l'emploi, en français soigné.

RÈGLES :
1. VOUVOIEMENT si le texte s'adresse à un client / partenaire (sauf si l'utilisateur demande le tutoiement).
2. N'invente JAMAIS de dates, montants, noms, chiffres non fournis dans la demande.
3. Si des infos manquent pour un texte précis (dates de congés, montant, destinataire…),
   rédige la meilleure version possible SANS inventer, ou signale brièvement ce qui manque.
4. Adapte le format :
   - post LinkedIn → accroche + 2–4 paragraphes courts + éventuelle conclusion
   - compte-rendu → structuré (contexte, points, décisions, suite)
   - message / SMS → court
   - note / annonce → clair et professionnel
5. Pas de markdown excessif (# ## **) sauf si utile pour un CR ; pour LinkedIn, texte naturel.
6. Pas de blabla d'intro ("Voici un texte que j'ai rédigé…") : donne DIRECTEMENT le texte demandé.
7. Si l'historique montre une demande de reformulation (plus court, plus pro, plus chaleureux…),
   repartir du dernier texte et appliquer UNIQUEMENT la modification demandée.
"""


async def run_redaction_agent(payload: dict) -> dict:
    instruction = (payload.get("instruction") or "").strip()
    request_id = payload.get("request_id")
    user_name = (payload.get("user_name") or "").strip()
    history = payload.get("conversation_history") or ""

    if not instruction:
        return {
            "success": False,
            "message": "Que souhaitez-vous que je rédige ?",
            "request_id": request_id,
        }

    try:
        today = datetime.now().strftime("%d/%m/%Y")
        user_msg = f"Date du jour : {today}\n"
        if user_name:
            user_msg += f"Auteur / signataire possible : {user_name}\n"
        if history:
            user_msg += f"\n=== HISTORIQUE (pour reformulations) ===\n{history}\n"
        user_msg += f"\n=== DEMANDE ===\n{instruction}\n"
        user_msg += "\nRédige le texte demandé, prêt à copier."

        response = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.5,
            max_tokens=1200,
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            text = "Je n'ai pas pu générer le texte. Reformulez votre demande."

        return {
            "success": True,
            "title": "Rédaction",
            "message": text,
            "content": text,
            "request_id": request_id,
        }
    except Exception as e:
        print(f"[Redaction] error: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "message": f"Erreur rédaction : {e}",
            "request_id": request_id,
        }
