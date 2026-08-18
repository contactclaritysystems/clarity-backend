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
- "redaction" : rédiger un contenu long qui n'est PAS un email (post LinkedIn, note, compte-rendu, document, message Slack/Teams non-email…)
- "assistant" : questions, conseils, explications, idées, résumés, reformulations, calculs, aide générale
- "planning" : rendez-vous, réunions, agenda, disponibilité, planifier un créneau
- "relance" : rappel personnel, "fais-moi penser", "rappelle-moi", "n'oublie pas", relancer un client, reminder

═══════════════════════════════════════
RÈGLES DE ROUTAGE (dans l'ordre)
═══════════════════════════════════════
1. Si la demande implique d'ENVOYER ou PRÉPARER un email → "mail"
2. Si c'est explicitement un rendez-vous / une réunion à planifier → "planning"
3. Si c'est une relance / un suivi d'action → "relance"
4. Si c'est une rédaction de contenu (LinkedIn, CR, note…) → "redaction"
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
                {"role": "system", "content": SYSTEM_PROMPT},
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

        # Pour l'instant planning/relance n'ont pas encore d'agent dédié
        # → on les route vers assistant avec une instruction claire
        # (prépare le terrain pour plus tard)
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
            "contacte", "réponds à", "reponds a", "relance par mail", "mets en copie"
        ]
        redaction_words = ["linkedin", "compte-rendu", "compte rendu", "rédige un", "redige un", "post"]
        planning_words = ["rendez-vous", "rendez vous", "réunion", "reunion", "agenda", "planifie", "disponible"]

        if any(w in lower for w in mail_words):
            agent = "mail"
        elif any(w in lower for w in redaction_words):
            agent = "redaction"
        elif any(w in lower for w in planning_words):
            agent = "assistant"  # planning pas encore implémenté
        else:
            agent = "assistant"

        return {
            "agent": agent,
            "instruction": instruction,
            "confidence": 0.6,
            "request_id": request_id,
            "success": True
        }
