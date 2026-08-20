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

RÈGLE MÉMOIRE (critique) :
- La MÉMOIRE BUSINESS contient d'éventuelles anciennes infos (ex: offre 7 jours gratuits).
- Si l'utilisateur dit "nouvelle offre", "offre de la rentrée", "notre offre" SANS détailler,
  tu ne dois PAS considérer que la mémoire suffit à elle seule.
  → has_enough = false
  → question polie qui soit :
     a) demande en quoi consiste CETTE offre, OU
     b) confirme : "S'agit-il toujours de [résumé mémoire] ?"
- Tu peux utiliser la mémoire SEULEMENT si l'utilisateur confirme, ou s'il reparle
  clairement de la même offre déjà décrite dans l'historique de CETTE conversation.

has_enough = true seulement si, dans la demande + historique de session (+ mémoire
confirmée), tu as le sujet concret de CETTE rédaction.

has_enough = false si vague ("nouvelle offre", "post pour mon produit") sans détail
dans l'historique de session.

question = une seule phrase, vouvoiement.
brief = ce qui est acquis pour CETTE demande (ne pas coller toute la mémoire
comme si c'était validé pour une "nouvelle" offre).

{
  "has_enough": true/false,
  "question": "null ou une question",
  "brief": "court",
  "doc_type": "linkedin|offre|message|compte_rendu|note|autre"
}
"""


WRITE_SYSTEM = """Tu es le rédacteur de Clarity Systems (SaaS français premium).

INTERDICTIONS (non négociables) :
- N'ajoute AUCUN bénéfice non écrit noir sur blanc dans le brief / historique.
- Phrases INTERDITES si non fournies par l'utilisateur :
  "simplifier vos processus", "améliorer la collaboration", "analyses en temps réel",
  "interface intuitive", "gagner en productivité", "transformer votre entreprise",
  "fonctionnalités avancées", et tout jargon SaaS générique.
- Si les seuls faits sont "offre de la rentrée" + "7 jours gratuits" (+ éventuellement
  un produit nommé), le texte ne doit contenir QUE ces éléments.

STYLE :
- Court (3–6 lignes pour un post).
- Concret, humain, pro.
- Vouvoiement.
- 0–1 emoji max. 0–2 hashtags liés au sujet.
- Texte final UNIQUEMENT, sans préambule.

BON exemple (faits: rentrée, 7 jours gratuits, agent admin pour patrons) :
"Offre de rentrée Clarity : 7 jours gratuits pour tester notre agent qui vous
aide sur l'administratif au quotidien.
Sans engagement — voyez par vous-même si vous gagnez du temps.
#ClaritySystems #Rentrée2026"

MAUVAIS exemple : tout post qui invente collaboration / analytics / transformation.
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
    user_msg += ("\nRédige le texte final. Rappel: si un bénéfice n'apparaît pas dans le brief ou l'historique, il est INTERDIT dans le texte. Post court, factuel.")

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



def is_vague_offer_request(instruction: str, history: str) -> bool:
    """True si demande d'offre/post trop vague et aucun détail dans la session."""
    inst = (instruction or "").lower()
    hist = (history or "").lower()
    session = inst + " " + hist

    vague_triggers = [
        "nouvelle offre",
        "nouvel offre",
        "offre de la rentrée",
        "offre de rentree",
        "offre de rentrée",
        "notre offre",
        "mon offre",
        "notre nouvelle offre",
        "ma nouvelle offre",
        "lancer une offre",
        "annonce mon offre",
        "annonce notre offre",
    ]
    if not any(t in inst for t in vague_triggers):
        # aussi: "post pour mon offre" sans détail
        if "offre" in inst and len(inst) < 80:
            pass  # continue check
        else:
            return False

    # Détails concrets déjà donnés dans la conversation
    detail_markers = [
        "jour gratuit", "jours gratuits", "7 jour", "essai gratuit",
        "€", "euro", "euros", "%", "mois à", "par mois",
        "agent ia", "agent d'ia", "administratif", "patrons",
        "dirig", "prix", "tarif", "abonnement",
    ]
    if any(m in session for m in detail_markers):
        return False

    # Historique avec une vraie description (réponse utilisateur substantielle)
    if hist and len(hist.strip()) > 120 and any(
        m in hist for m in ("gratuit", "agent", "€", "jour", "aide", "admin")
    ):
        return False

    return True


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

    # BLOCAGE DUR : offre / rentrée sans détail de session → question obligatoire
    if is_vague_offer_request(instruction, history) and not is_rewrite_request(instruction):
        facts = load_memory(payload.get("user_id"))
        memory_text = memory_as_text(facts)
        if memory_text:
            msg = (
                "Souhaitez-vous que je m'appuie sur l'offre déjà enregistrée dans "
                "votre espace Clarity, ou s'agit-il d'une offre différente à me décrire ?"
            )
            brief = "Nouvelle offre — confirmation requise"
        else:
            msg = "Bien sûr. En quoi consiste cette offre (promesse, public, offre concrète) ?"
            brief = "Nouvelle offre — en attente de précisions"
        return {
            "success": False,
            "reason": "missing_content",
            "message": msg,
            "brief": brief,
            "partial": {"brief": brief},
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

        # Garde-fou : ne PAS forcer l'écriture si la demande parle d'une
        # "nouvelle offre" / "offre de la rentrée" sans détail dans l'historique
        inst_l = (instruction or "").lower()
        hist_l = (history or "").lower()
        vague_new = any(
            p in inst_l
            for p in (
                "nouvelle offre",
                "nouvel offre",
                "offre de la rentrée",
                "offre de rentree",
                "notre offre",
                "mon offre",
            )
        )
        # détails concrets donnés dans CETTE conversation (pas seulement mémoire)
        session_detail = any(
            p in hist_l or p in inst_l
            for p in (
                "jour", "gratuit", "€", "euro", "%", "agent", "essai",
                "prix", "mois", "semaine", "aide", "admin",
            )
        )
        if vague_new and not session_detail and not hist_l.strip():
            has_enough = False
            if not question:
                if memory_text:
                    question = (
                        "Souhaitez-vous que je m'appuie sur l'offre déjà enregistrée "
                        "dans votre espace, ou s'agit-il d'une offre différente à me décrire ?"
                    )
                else:
                    question = (
                        "Bien sûr. En quoi consiste cette offre de la rentrée ?"
                    )
            if not brief:
                brief = "Nouvelle offre — en attente de précisions"

        # Garde-fou inverse : brief de SESSION déjà riche → on peut rédiger
        brief_l = (brief + " " + hist_l).lower()
        signals = 0
        for w in ("essai", "gratuit", "agent", "ia", "admin", "patron", "jour", "prix"):
            if w in brief_l:
                signals += 1
        if len(brief) >= 40 and signals >= 2 and hist_l.strip():
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
