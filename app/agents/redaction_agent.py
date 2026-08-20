"""
Agent Rédaction Clarity — premium
Collecte progressive : 1 question à la fois, brief visible, zéro invention.
"""

import json
import os
from datetime import datetime
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


ANALYZE_SYSTEM = """Tu prépares une rédaction pour Clarity (SaaS pro français).
Réponds UNIQUEMENT en JSON valide.

Objectif : rédiger DÈS QUE POSSIBLE, sans inventer, sans harceler l'utilisateur.

has_enough = true DÈS QUE tu as :
- un type de texte (post, offre, message…) OU que c'est implicite
- ET le sujet principal (produit / offre / annonce) avec au moins UNE promesse
  ou description (ex: "7 jours gratuits", "agent IA admin pour patrons")

Dès que le brief ressemble à :
"Post LinkedIn · 7 jours gratuits · agent IA qui aide les patrons sur l'admin"
→ has_enough DOIT être true. N'ajoute PAS de questions sur avantages, CTA,
ton, emojis, public précis, etc. Ces détails sont OPTIONNELS : le rédacteur
écrira un bon post avec ce qui est là.

has_enough = false UNIQUEMENT si le sujet est encore vide ou incompréhensible
(ex: "fais un post" sans aucun thème, "rédige mon offre" sans dire laquelle).

question = une seule phrase, vouvoiement. null si has_enough.
brief = résumé court de tout ce qui est acquis.

{
  "has_enough": true/false,
  "question": "null ou une question",
  "brief": "ce qui est déjà compris, court",
  "doc_type": "linkedin|offre|message|compte_rendu|note|autre"
}
"""


WRITE_SYSTEM = """Tu es le rédacteur de Clarity Systems (SaaS français premium).

RÈGLES ABSOLUES :
1. N'invente JAMAIS de fonctionnalités, bénéfices, chiffres, dates ou slogans
   qui ne sont PAS dans le brief / l'historique / la mémoire.
2. Si tu n'as que 2–3 faits (ex: "7 jours gratuits", "agent IA admin pour patrons"),
   le post doit être COURT et ne parler QUE de ça. Pas de liste inventée
   (collaboration, analyses temps réel, interface intuitive, etc.).
3. Interdit d'ajouter des avantages génériques SaaS non mentionnés.
4. VOUVOIEMENT si le texte s'adresse au client, sauf demande contraire.
5. Donne DIRECTEMENT le texte (pas "Voici un post…").
6. Emojis : 0 à 2 max, seulement si le ton s'y prête. Pas de mur d'emojis.
7. Hashtags : 0 à 3, optionnels, liés au sujet réel.
8. Reformulation : appliquer uniquement la consigne.

Exemple de BON post si le brief = "Post · 7 jours gratuits · agent IA admin patrons" :
un texte court qui annonce l'essai gratuit et le rôle de l'agent, rien de plus.
"""


async def analyze_request(instruction: str, history: str, memory_text: str = "") -> dict:
    user = f"Dernière message de l'utilisateur : {instruction}"
    if memory_text:
        user += f"\n\n=== MÉMOIRE BUSINESS ===\n{memory_text}"
    if history:
        user += f"\n\n=== HISTORIQUE DE CETTE CONVERSATION ===\n{history}"
    user += (
        "\n\nMets à jour le brief avec TOUT ce qui a déjà été dit "
        "(historique inclus). Une seule question s'il manque encore le cœur."
    )
    response = get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": ANALYZE_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.15,
        max_tokens=450,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


async def write_text(
    instruction: str,
    history: str,
    user_name: str,
    brief: str = "",
    memory_text: str = "",
) -> str:
    today = datetime.now().strftime("%d/%m/%Y")
    user_msg = f"Date : {today}\n"
    if user_name:
        user_msg += f"Auteur possible : {user_name}\n"
    if memory_text:
        user_msg += f"\n=== MÉMOIRE BUSINESS ===\n{memory_text}\n"
    if brief:
        user_msg += f"\n=== BRIEF ACQUIS ===\n{brief}\n"
    if history:
        user_msg += f"\n=== HISTORIQUE ===\n{history}\n"
    user_msg += f"\n=== DEMANDE ===\n{instruction}\n"
    user_msg += "\nRédige le texte final, prêt à copier. UNIQUEMENT avec les faits ci-dessus, sans rien inventer."

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
    user_id = payload.get("user_id")

    if not instruction:
        return {
            "success": False,
            "reason": "missing_content",
            "message": "Que souhaitez-vous que je rédige ?",
            "brief": "",
            "request_id": request_id,
        }

    try:
        facts = load_memory(user_id)
        memory_text = memory_as_text(facts)

        if is_rewrite_request(instruction) and history:
            text_out = await write_text(
                instruction, history, user_name, memory_text=memory_text
            )
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
        doc_type = (analysis.get("doc_type") or "").strip()

        # Garde-fou : si le brief est déjà substantiel, on rédige
        brief_l = (brief + " " + instruction + " " + (history or "")).lower()
        signals = 0
        for w in ("post", "linkedin", "offre", "essai", "gratuit", "agent", "ia",
                  "admin", "patron", "jour", "mail", "rdv", "rappel", "clarity"):
            if w in brief_l:
                signals += 1
        if len(brief) >= 40 and signals >= 2:
            has_enough = True

        if not has_enough:
            if not question:
                question = "Bien sûr. De quoi traite exactement ce texte ?"
            if not brief:
                brief = "Rédaction — en cours de précision"
            return {
                "success": False,
                "reason": "missing_content",
                "message": question,
                "brief": brief,
                "partial": {
                    "brief": brief,
                    "doc_type": doc_type or None,
                },
                "request_id": request_id,
            }

        text_out = await write_text(
            instruction,
            history,
            user_name,
            brief=brief,
            memory_text=memory_text,
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
            "brief": brief,
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
