"""
Agent Mail Clarity — Version Premium
"""

import json
import os
import re
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
    """Écrit dans agent_progress pour le polling du frontend."""
    if not request_id:
        return
    sb = get_supabase()
    if not sb:
        return
    try:
        sb.table("agent_progress").insert({
            "request_id": request_id,
            "agent": "mail",
            "status": status,
            "message": message or ""
        }).execute()
        print(f"[Progress] {status} — {message}")
    except Exception as e:
        print(f"[Progress] erreur: {e}")


INTENT_SYSTEM = """Tu es le module d'analyse de Clarity Systems.
Extrais les infos de la dictée. Réponds UNIQUEMENT en JSON.

Règle CRITIQUE pour has_enough_content :
- true UNIQUEMENT s'il y a un vrai message à transmettre (ex: "dis-lui que je serai en retard", "remercie-le")
- false si la demande est juste "envoie un mail à X" sans contenu
- Détecte aussi les copies : "mets Paul en copie" → cc_names

{
  "to_names": ["prénom"],
  "cc_names": ["prénom"],
  "relationship": null,
  "tone": "professionnel",
  "has_enough_content": true,
  "content_summary": "résumé",
  "raw_instruction": "demande originale"
}
"""


async def extract_intent(instruction: str, user_name: str) -> dict:
    response = get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": INTENT_SYSTEM},
            {"role": "user", "content": f"Dictée : {instruction}\nExpéditeur : {user_name}"}
        ],
        temperature=0.1,
        max_tokens=400,
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)


def search_contacts(name: str, user_id: Optional[str] = None) -> List[dict]:
    sb = get_supabase()
    if not sb:
        return []
    try:
        name_clean = name.strip()
        query = sb.table("contacts").select("id, full_name, email, company")
        if user_id:
            query = query.eq("user_id", user_id)
        query = query.ilike("full_name", f"%{name_clean}%")
        result = query.limit(10).execute()
        print(f"[Mail Agent] Recherche '{name_clean}' → {len(result.data or [])} résultat(s)")
        return result.data or []
    except Exception as e:
        print(f"[Mail Agent] Erreur Supabase: {e}")
        return []


def resolve_contacts(names: List[str], user_id: Optional[str] = None) -> Dict[str, Any]:
    found, ambiguous, not_found = [], [], []
    for name in names:
        results = search_contacts(name, user_id)
        if len(results) == 0:
            not_found.append(name)
        elif len(results) == 1:
            found.append(results[0])
        else:
            ambiguous.append({"name": name, "contacts": results})
    return {"found": found, "ambiguous": ambiguous, "not_found": not_found}


WRITE_SYSTEM = """Tu rédiges un email AU NOM de l'utilisateur, à la PREMIÈRE PERSONNE (je/moi).

Règles STRICTES de mise en page :
- Utilise de VRAIS sauts de ligne (\n) entre les blocs
- Structure obligatoire :
  1. Salutation (ex: Bonjour Antoine,)
  2. Ligne vide
  3. Corps du message (1 à 3 phrases max, claires)
  4. Ligne vide
  5. Formule de politesse (ex: Cordialement,)
  6. Signature = nom exact fourni
- Jamais tout collé sur une seule ligne
- Jamais "l'utilisateur"
- Respecte le ton demandé
- JSON uniquement : {"subject": "...", "body": "..."}

Exemple de body :
"Bonjour Antoine,\n\nJe tenais à te confirmer que le passage est prévu demain matin.\n\nCordialement,\nAnthony"
"""


async def write_email(content_summary: str, user_name: str, to_name: str = "",
                      relationship: Optional[str] = None, tone: Optional[str] = None) -> dict:
    parts = [
        f"Tu écris ce mail AU NOM de : {user_name}",
        f"Contenu : {content_summary}",
        f"Destinataire : {to_name or 'le destinataire'}",
        "Écris à la 1re personne (je/moi).",
        f"Signature obligatoire : {user_name}",
    ]
    if relationship:
        parts.append(f"Relation : {relationship}")
    if tone:
        parts.append(f"Ton : {tone}")

    response = get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": WRITE_SYSTEM},
            {"role": "user", "content": "\n".join(parts)}
        ],
        temperature=0.4,
        max_tokens=700,
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)


async def run_mail_agent(payload: dict) -> dict:
    instruction = (payload.get("instruction") or "").strip()
    request_id = payload.get("request_id")
    user_id = payload.get("user_id")
    user_name = (payload.get("user_name") or "").strip() or "Anthony"
    contact_id = payload.get("contact_id")

    if not instruction:
        return {"success": False, "message": "Aucune instruction reçue.", "request_id": request_id}

    try:
        intent = await extract_intent(instruction, user_name)
        to_names = intent.get("to_names") or []
        cc_names = intent.get("cc_names") or []
        relationship = intent.get("relationship")
        tone = intent.get("tone")
        has_enough_content = intent.get("has_enough_content", False)
        content_summary = intent.get("content_summary") or ""

        if not to_names and not contact_id:
            update_progress(request_id, "error", "Aucun destinataire")
            return {"success": False, "reason": "no_recipient", "request_id": request_id}

        if contact_id:
            resolved_to = [{"id": contact_id, "full_name": to_names[0] if to_names else "Contact", "email": ""}]
            sb = get_supabase()
            if sb:
                try:
                    res = sb.table("contacts").select("id, full_name, email").eq("id", contact_id).limit(1).execute()
                    if res.data:
                        resolved_to = [res.data[0]]
                except Exception:
                    pass
            ambiguous, not_found = [], []
        else:
            resolution = resolve_contacts(to_names, user_id)
            resolved_to = resolution["found"]
            ambiguous = resolution["ambiguous"]
            not_found = resolution["not_found"]

        if ambiguous:
            update_progress(request_id, "error", "Plusieurs contacts trouvés")
            return {"action": "choose_contact", "contacts": ambiguous[0]["contacts"], "request_id": request_id}

        if not_found:
            update_progress(request_id, "error", f"Contact introuvable : {not_found[0]}")
            return {
                "success": False,
                "reason": "contact_not_found",
                "contact_name": not_found[0],
                "request_id": request_id
            }

        main_contact = resolved_to[0] if resolved_to else {}
        contact_label = main_contact.get("full_name") or (to_names[0] if to_names else "")
        update_progress(request_id, "contact_found", contact_label)

        if not has_enough_content:
            return {
                "success": False,
                "reason": "missing_content",
                "contact_id": main_contact.get("id"),
                "contact_name": contact_label,
                "request_id": request_id
            }

        update_progress(request_id, "generating_mail", contact_label)

        to_email = main_contact.get("email") or ""
        to_name = contact_label

        # Résoudre le CC si présent
        cc_email = None
        cc_name = None
        if cc_names:
            cc_resolution = resolve_contacts(cc_names, user_id)
            if cc_resolution["found"]:
                cc_email = cc_resolution["found"][0].get("email")
                cc_name = cc_resolution["found"][0].get("full_name") or cc_names[0]
            else:
                cc_name = cc_names[0]

        email = await write_email(
            content_summary=content_summary,
            user_name=user_name,
            to_name=to_name,
            relationship=relationship,
            tone=tone
        )

        body = email.get("body") or ""
        body = body.replace("L'utilisateur", user_name)
        body = body.replace("l'utilisateur", user_name)
        body = body.replace("L’utilisateur", user_name)
        body = body.replace("l’utilisateur", user_name)
        body = re.sub(r"\[Votre[^\]]*\]", user_name, body, flags=re.IGNORECASE)
        # Si tout est collé sur une ligne, on force une structure minimale
        if "\n" not in body and "," in body:
            # Ex: "Bonjour X, texte. Cordialement, Nom"
            body = body.replace(". ", ".\n\n")
            body = re.sub(r",\s*(Cordialement|Bien à vous|Amicalement)", r",\n\n\1", body, flags=re.IGNORECASE)
        if user_name.lower() not in body[-50:].lower():
            body = body.rstrip() + "\n\n" + user_name

        update_progress(request_id, "ready", contact_label)

        result = {
            "to": to_email,
            "subject": email.get("subject") or "Sans objet",
            "body": body,
            "contact_name": to_name,
            "success": True,
            "request_id": request_id
        }
        if cc_name:
            result["cc_name"] = cc_name
        if cc_email:
            result["cc"] = cc_email
        return result

    except Exception as e:
        print(f"[Mail Agent error] {e}")
        import traceback
        traceback.print_exc()
        update_progress(request_id, "error", str(e))
        return {"success": False, "message": f"Erreur: {str(e)}", "request_id": request_id}
