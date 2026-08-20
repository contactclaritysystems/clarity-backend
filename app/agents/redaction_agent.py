"""
Agent Rédaction Clarity — natif Render
Premium : ne rédige que si les infos suffisent, sinon pose UNE question claire.
N'invente jamais dates, montants, détails produit, etc.
"""

import json
import os
from datetime import datetime
from typing import Optional
from openai import OpenAI
from dotenv import load_dotenv
from app.memory import load_memory, memory_as_text, extract_and_save_memory

load_dotenv()
MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")


def get_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY manquante")
    return OpenAI(api_key=api_key)


ANALYZE_SYSTEM = """Tu analyses une demande de RÉDACTION pour Clarity (SaaS pro français).
Réponds UNIQUEMENT en JSON valide.

Décide si on a assez d'éléments pour rédiger un texte UTILE et HONNÊTE,
sans inventer de faits (offre, prix, dates, bénéfices, public cible…).

has_enough = true SEULEMENT si la demande + l'historique + la MÉMOIRE BUSINESS
fournissent le fond nécessaire pour rédiger SANS inventer.

has_enough = false si il manque encore des éléments clés
→ "question" = UNE seule question polie en vouvoiement, ciblée, pour combler LE prochain trou
  (on en posera d'autres au tour suivant si besoin).

Si la mémoire contient déjà current_offer / product_name, NE PAS redemander
ce qui est déjà connu, sauf si l'utilisateur parle d'une NOUVELLE offre.

{
  "has_enough": true/false,
  "question": "null ou une question en français, vouvoiement",
  "doc_type": "linkedin|compte_rendu|message|offre|note|autre",
  "brief": "résumé de ce qu'on sait déjà"
}
"""


WRITE_SYSTEM = """Tu es le module de rédaction de Clarity Systems (SaaS français ultra-premium).

RÈGLES STRICTES :
1. N'invente JAMAIS de faits non fournis (prix, dates, fonctionnalités, noms, chiffres).
2. VOUVOIEMENT dans les textes adressés à un client, sauf demande contraire.
3. Donne DIRECTEMENT le texte demandé (pas "Voici un texte…").
4. Adapte le format (LinkedIn, CR, message, offre, note).
5. Si l'historique contient une reformulation (plus court, plus pro…),
   repartir du dernier texte et appliquer uniquement la modification.
6. Français soigné, ton professionnel, clair, premium.
"""


async def analyze_request(instruction: str, history: str, memory_text: str = "") -> dict:
    user = f"Demande : {instruction}"
    if memory_text:
        user += f"\n\n=== MÉMOIRE BUSINESS (persistante) ===\n{memory_text}"
    if history:
        user += f"\n\nHistorique récent :\n{history}"
    response = get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": ANALYZE_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.1,
        max_tokens=400,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


async def write_text(instruction: str, history: str, user_name: str, brief: str = "", memory_text: str = "") -> str:
    today = datetime.now().strftime("%d/%m/%Y")
    user_msg = f"Date du jour : {today}\n"
    if user_name:
        user_msg += f"Auteur possible : {user_name}\n"
    if memory_text:
        user_msg += f"\n=== MÉMOIRE BUSINESS ===\n{memory_text}\n"
    if brief:
        user_msg += f"Brief déjà connu : {brief}\n"
    if history:
        user_msg += f"\n=== HISTORIQUE ===\n{history}\n"
    user_msg += f"\n=== DEMANDE ===\n{instruction}\n"
    user_msg += "\nRédige le texte, prêt à copier. N'invente aucun fait manquant."

    response = get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": WRITE_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.45,
        max_tokens=1200,
    )
    return (response.choices[0].message.content or "").strip()


def is_rewrite_request(instruction: str) -> bool:
    t = (instruction or "").lower().strip()
    keys = [
        "plus court", "plus long", "plus pro", "plus professionnel",
        "plus chaleureux", "plus formel", "reformule", "réécris", "reecris",
        "autre version", "change le ton", "raccourci", "allonge",
    ]
    return any(k in t for k in keys)


async def run_redaction_agent(payload: dict) -> dict:
    instruction = (payload.get("instruction") or "").strip()
    request_id = payload.get("request_id")
    user_name = (payload.get("user_name") or "").strip()
    history = payload.get("conversation_history") or ""

    if not instruction:
        return {
            "success": False,
            "reason": "missing_content",
            "message": "Que souhaitez-vous que je rédige ?",
            "request_id": request_id,
        }

    try:
        user_id = payload.get("user_id")
        facts = load_memory(user_id)
        memory_text = memory_as_text(facts)

        # Reformulation sur un texte déjà produit → on rédige directement
        if is_rewrite_request(instruction) and history:
            text_out = await write_text(instruction, history, user_name, memory_text=memory_text)
            if not text_out:
                text_out = "Je n'ai pas pu reformuler. Précisez votre demande."
            await extract_and_save_memory(user_id, instruction, text_out, history)
            return {
                "success": True,
                "title": "Rédaction",
                "message": text_out,
                "content": text_out,
                "request_id": request_id,
            }

        analysis = await analyze_request(instruction, history, memory_text=memory_text)
        has_enough = bool(analysis.get("has_enough"))
        question = (analysis.get("question") or "").strip()
        brief = (analysis.get("brief") or "").strip()

        if not has_enough:
            if not question:
                question = (
                    "Bien sûr. Pouvez-vous me préciser le sujet et les points "
                    "essentiels à inclure ?"
                )
            return {
                "success": False,
                "reason": "missing_content",
                "message": question,
                "request_id": request_id,
            }

        text_out = await write_text(
            instruction, history, user_name, brief=brief, memory_text=memory_text
        )
        if not text_out:
            return {
                "success": False,
                "message": "Je n'ai pas pu générer le texte. Reformulez votre demande.",
                "request_id": request_id,
            }

        await extract_and_save_memory(user_id, instruction, text_out, history)

        return {
            "success": True,
            "title": "Rédaction",
            "message": text_out,
            "content": text_out,
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
