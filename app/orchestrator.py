"""
Orchestrateur Clarity — Version Premium
=======================================
Cerveau central : comprend l'intention, le contexte, les suivis,
et route vers le bon agent avec une instruction claire.
"""

import json
import os
from openai import OpenAI
from dotenv import load_dotenv
from app.capabilities import capabilities_prompt_block

load_dotenv()

MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")


def get_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY manquante")
    return OpenAI(api_key=api_key)


SYSTEM_PROMPT = """Tu es l'orchestrateur de Clarity Systems, un SaaS français ultra-premium d'assistance administrative vocale.

Ton rôle : comprendre EXACTEMENT ce que veut l'utilisateur et décider quel agent doit traiter la demande.

═══════════════════════════════════════
AGENTS DISPONIBLES
═══════════════════════════════════════
- "mail" : tout ce qui concerne un email (envoyer, répondre, relancer, préparer un mail, mettre quelqu'un en copie…)
- "redaction" : rédiger un texte, post, offre, compte-rendu, note SANS destinataire précis (ex: « rédige un post LinkedIn »). Ce n'est PAS l'assistant.
- "assistant" : questions, conseils, explications, idées, résumés, calculs, aide générale
  (PAS pour « rédige un post », « offre commerciale » seuls → redaction ; mais « message pour Antoine » → mail)
- "planning" : CRÉER un rendez-vous / réunion (pas consulter l'agenda)
- "relance" : CRÉER un rappel ("fais-moi penser", "rappelle-moi") — pas lister les rappels existants
- Pour CONSULTER agenda / rappels / contacts → "assistant"

═══════════════════════════════════════
RÈGLES DE ROUTAGE (dans l'ordre)
═══════════════════════════════════════
0. CONSULTATION vs ACTION (CRITIQUE) :
   - Si l'utilisateur DEMANDE UNE INFO / un résumé / une liste
     (ex: "qu'est-ce que j'ai comme RDV", "mes rappels en attente",
      "résume ma journée", "est-ce que j'ai le contact de…",
      "quels sont mes rendez-vous", "ai-je quelque chose demain")
     → TOUJOURS "assistant"
   - "planning" UNIQUEMENT pour CRÉER / MODIFIER / ANNULER un rendez-vous
     (ex: "ajoute un RDV", "planifie une réunion", "note un rendez-vous avec…")
   - "relance" UNIQUEMENT pour CRÉER un rappel
     (ex: "fais-moi penser", "rappelle-moi de…", "crée un rappel")

1. Si la demande implique d'ENVOYER, PRÉPARER un email/message À quelqu'un (ex: "message pour Antoine", "dis-lui que...", "préviens Paul") → "mail"
2. Si c'est CRÉER un rendez-vous / une réunion → "planning"
3. Si c'est CRÉER un rappel / "fais-moi penser" → "relance"
4. Si c'est rédiger SANS destinataire email (post, Insta, LinkedIn, offre, CR, SMS à publier, « message pour Instagram ») → "redaction"
   « message pour Insta / LinkedIn / Facebook / Stories » = REDACTION, jamais mail.
   Mail seulement si un DESTINATAIRE PERSONNE (Antoine, mon patron) ou « envoie un mail ».
5. Sinon → "assistant"

═══════════════════════════════════════
SUIVIS / CONTINUITÉ
═══════════════════════════════════════
Si l'historique montre qu'on vient de travailler avec un agent (ex: mail),
et que l'utilisateur dit :
- "non, plus court"
- "ajoute que…"
- "change le ton"
- "renvoie-le"
- "améliore"
→ Reste sur LE MÊME agent et reformule l'instruction en gardant le contexte.

═══════════════════════════════════════
MULTI-INTENTIONS
═══════════════════════════════════════
Si la demande contient DEUX actions clairement séparées
(ex: "envoie un mail à Antoine ET ajoute un RDV mardi"),
choisis l'intention PRINCIPALE (souvent le mail) et note l'autre dans "secondary_intent".

═══════════════════════════════════════
REFORMULATION
═══════════════════════════════════════
L'instruction renvoyée doit être claire, complète, et prête à être exécutée par l'agent.
Ne perds aucune information importante (noms, dates, ton, copies…).

═══════════════════════════════════════
FORMAT DE RÉPONSE (JSON uniquement)
═══════════════════════════════════════
{
  "agent": "mail" | "assistant" | "redaction" | "planning" | "relance",
  "instruction": "instruction claire et complète pour l'agent",
  "confidence": 0.0 à 1.0,
  "secondary_intent": null ou "planning" | "relance" | ...,
  "is_followup": true/false,
  "reasoning": "courte explication de ton choix"
}
"""


async def run_orchestrator(payload: dict) -> dict:
    instruction = (payload.get("instruction") or "").strip()
    request_id = payload.get("request_id")
    history = payload.get("conversation_history") or payload.get("history") or ""
    last_agent = payload.get("last_agent")  # fourni par le frontend si suivi

    if not instruction:
        return {
            "success": False,
            "message": "Aucune demande reçue.",
            "request_id": request_id
        }

    user_parts = [f"Demande de l'utilisateur :\n{instruction}"]
    if history:
        user_parts.append(f"Historique récent :\n{history}")
    if last_agent:
        user_parts.append(f"Dernier agent utilisé : {last_agent}")

    user_content = "\n\n".join(user_parts)

    try:
        response = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + capabilities_prompt_block()},
                {"role": "user", "content": user_content}
            ],
            temperature=0.1,
            max_tokens=400,
            response_format={"type": "json_object"}
        )

        data = json.loads(response.choices[0].message.content)

        agent = data.get("agent", "assistant")
        allowed = ("mail", "assistant", "redaction", "planning", "relance")
        if agent not in allowed:
            agent = "assistant"

        final_agent = agent

        return {
            "agent": final_agent,
            "instruction": data.get("instruction") or instruction,
            "confidence": float(data.get("confidence") or 0.8),
            "secondary_intent": data.get("secondary_intent"),
            "is_followup": bool(data.get("is_followup", False)),
            "reasoning": data.get("reasoning") or "",
            "requested_agent": agent,
            "request_id": request_id,
            "success": True
        }

    except Exception as e:
        print(f"[Orchestrator error] {e}")
        # Fallback déterministe
        lower = instruction.lower()
        mail_words = [
            "mail", "email", "e-mail", "envoie", "envoyer", "écris à", "ecris a",
            "contacte", "réponds à", "reponds a", "relance par mail", "mets en copie",
            "message pour", "message a", "message à", "dis-lui", "dis lui", "dit-lui", "dit lui",
            "préviens", "previens", "prévenez", "dis à", "dis a",
        ]
        redaction_words = ["linkedin", "instagram", "insta", "facebook", "stories", "compte-rendu", "compte rendu", "rédige", "redige", "post", "offre", "annonce", "texte pour le site"]
        planning_create = ["ajoute un rdv", "ajoute un rendez-vous", "planifie", "note un rdv", "prend un rdv", "prends un rdv"]
        relance_create = ["fais-moi penser", "fais moi penser", "rappelle-moi", "rappelle moi", "crée un rappel", "creer un rappel"]
        question_words = ["qu'est-ce", "quest-ce", "quels", "quelles", "ai-je", "est-ce que", "résume", "resume", "combien", "montre", "liste"]

        if any(w in lower for w in question_words):
            agent = "assistant"
        elif any(w in lower for w in redaction_words):
            agent = "redaction"
        elif any(w in lower for w in mail_words):
            agent = "mail"
        elif any(w in lower for w in relance_create):
            agent = "relance"
        elif any(w in lower for w in planning_create):
            agent = "planning"
        else:
            agent = "assistant"

        return {
            "agent": agent,
            "instruction": instruction,
            "confidence": 0.6,
            "request_id": request_id,
            "success": True
        }
