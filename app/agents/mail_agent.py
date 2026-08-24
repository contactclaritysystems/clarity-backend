"""
Agent Mail Clarity — Version Premium
"""

import json
import difflib
import os
import re
from typing import Optional, List, Dict, Any
from openai import OpenAI
from dotenv import load_dotenv
from supabase import create_client, Client
from app.writing_styles import get_style, style_prompt_block

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
Extrais les infos de la dictée pour préparer un MESSAGE / MAIL. Réponds UNIQUEMENT en JSON.

Règles has_enough_content :
- true s'il y a QUELQUE CHOSE à transmettre : "dit-lui rdv demain 8h", "dis-lui que je serai en retard", "préviens-le que...", "message pour X : ..."
- false UNIQUEMENT si la demande est vide de contenu : "envoie un mail à Antoine" sans autre info
- "écrit un message pour Anthony, dit lui rdv demain 8h" → to_names: ["Anthony"], has_enough_content: true, content_summary: "RDV demain à 8h"
- Détecte les copies : "mets Paul en copie" → cc_names

{
  "to_names": ["prénom"],
  "cc_names": [],
  "relationship": null,
  "tone": null,
  "has_enough_content": true,
  "content_summary": "résumé du message à envoyer",
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


def _norm_name(s: str) -> str:
    """Normalise pour comparaison (minuscules, sans accents basiques, espaces simples)."""
    if not s:
        return ""
    s = s.lower().strip()
    for a, b in (
        ("é", "e"), ("è", "e"), ("ê", "e"), ("ë", "e"),
        ("à", "a"), ("â", "a"), ("ä", "a"),
        ("î", "i"), ("ï", "i"),
        ("ô", "o"), ("ö", "o"),
        ("ù", "u"), ("û", "u"), ("ü", "u"),
        ("ç", "c"), ("ÿ", "y"),
    ):
        s = s.replace(a, b)
    return " ".join(s.split())


def search_contacts(name: str, user_id: Optional[str] = None) -> List[dict]:
    """Recherche tolérante: exact/partiel, puis fautes de frappe / orthographes proches."""
    sb = get_supabase()
    if not sb:
        return []
    try:
        name_clean = (name or "").strip()
        if not name_clean:
            return []

        cols = "id, full_name, email, company, writing_style_key"
        base = sb.table("contacts").select(cols)
        if user_id:
            base = base.eq("user_id", user_id)

        # 1) Correspondance partielle classique
        result = base.ilike("full_name", f"%{name_clean}%").limit(10).execute()
        rows = result.data or []
        if rows:
            print(f"[Mail Agent] Recherche '{name_clean}' → {len(rows)} (ilike)")
            return rows

        # 2) Préfixe (Anthonyy → Anthon%)
        if len(name_clean) >= 3:
            prefix = name_clean[: max(3, len(name_clean) - 1)]
            q2 = sb.table("contacts").select(cols)
            if user_id:
                q2 = q2.eq("user_id", user_id)
            rows = (q2.ilike("full_name", f"{prefix}%").limit(15).execute().data) or []
            if len(rows) == 1:
                print(f"[Mail Agent] Recherche '{name_clean}' → 1 (prefix {prefix})")
                return rows
            if rows:
                # on continue pour scorer si plusieurs
                pass

        # 3) Fuzzy sur le carnet de l'utilisateur (fautes, Michael/Mickael)
        q3 = sb.table("contacts").select(cols)
        if user_id:
            q3 = q3.eq("user_id", user_id)
        all_rows = (q3.limit(300).execute().data) or []
        if not all_rows:
            print(f"[Mail Agent] Recherche '{name_clean}' → 0")
            return []

        needle = _norm_name(name_clean)
        needle_tokens = needle.split()
        scored = []
        for c in all_rows:
            full = _norm_name(c.get("full_name") or "")
            if not full:
                continue
            # ratio global + ratio sur le prenom (1er mot)
            r_full = difflib.SequenceMatcher(None, needle, full).ratio()
            first = full.split()[0] if full.split() else full
            r_first = difflib.SequenceMatcher(None, needle_tokens[0] if needle_tokens else needle, first).ratio()
            # bonus si le debut correspond
            bonus = 0.08 if first.startswith(needle[:3]) or needle.startswith(first[:3]) else 0.0
            score = max(r_full, r_first) + bonus
            if score >= 0.72:
                scored.append((score, c))

        scored.sort(key=lambda x: -x[0])
        best = [c for _, c in scored[:5]]
        print(
            f"[Mail Agent] Recherche '{name_clean}' → {len(best)} (fuzzy) "
            f"top={scored[0][0]:.2f}" if scored else f"[Mail Agent] Recherche '{name_clean}' → 0 (fuzzy)"
        )
        return best
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
- N'invente JAMAIS de dates, montants, lieux ou détails non fournis dans le contenu
- Si les dates de congés ne sont pas précisées, demande-les implicitement ou reste vague ("sur la période souhaitée") SANS inventer du 10 au 14 avril
- JSON uniquement : {"subject": "...", "body": "..."}

Exemple de body :
"Bonjour Antoine,\n\nJe tenais à te confirmer que le passage est prévu demain matin.\n\nCordialement,\nAnthony"
"""


async def write_email(content_summary: str, user_name: str, to_name: str = "",
                      relationship: Optional[str] = None, tone: Optional[str] = None,
                      style_block: str = "") -> dict:
    system = WRITE_SYSTEM
    if style_block:
        system = (
            WRITE_SYSTEM
            + "\n\n=== STYLE UTILISATEUR (PRIORITE ABSOLUE SUR TOUT LE RESTE) ===\n"
            + style_block
            + "\nLe STYLE est la seule source de verite (langue, ton, formules). "
            "Si l'exemple est en anglais, tout le mail en anglais. "
            "N'impose jamais Bonjour/Cordialement si l'exemple ne les utilise pas. "
            "CRITIQUE : le contenu fourni peut etre en tutoiement. "
            "Tu DOIS le REFORMULER pour coller au style "
            "(ex: 'peux-tu passer' → 'pourriez-vous passer' si le style vouvoie), "
            "sans changer le sens."
        )
    parts = [
        f"Tu ecris ce mail AU NOM de : {user_name}",
        f"Contenu a transmettre (reformule selon le STYLE si besoin, ne rien inventer) : {content_summary}",
        f"Destinataire : {to_name or 'le destinataire'}",
        "Ecris a la 1re personne (je/moi ou I/me selon la langue du style).",
        f"Signature obligatoire : {user_name}",
    ]
    if relationship and not style_block:
        parts.append(f"Relation : {relationship}")
    if tone and not style_block:
        parts.append(f"Ton : {tone}")

    response = get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(parts)}
        ],
        temperature=0.2 if style_block else 0.4,
        max_tokens=700,
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)



async def rewrite_email_to_style(subject: str, body: str, style: dict, user_name: str, to_name: str) -> dict:
    """2e passe: force le style. Le modele ignore souvent le style au 1er jet."""
    if not style:
        return {"subject": subject, "body": body}
    example = (style.get("example_message") or "").strip()
    label = style.get("label") or style.get("key") or "style"
    if not example:
        return {"subject": subject, "body": body}
    system = (
        "Tu reecris un email pour coller EXACTEMENT au style demande. "
        "Tu reponds UNIQUEMENT en JSON: {\"subject\": \"...\", \"body\": \"...\"}. "
        "Regles: meme langue que l'exemple; meme tutoiement/vouvoiement; "
        "memes types de salutation et de formule de fin; "
        "ne change pas les faits (dates, heures, noms); "
        "ecris a la 1re personne; signature = nom fourni; "
        "si l'exemple vouvoie, INTERDIT d'utiliser tu/te/ton/ta."
    )
    user = (
        f"Style: {label}\n"
        f"Exemple a imiter:\n« {example} »\n\n"
        f"Destinataire: {to_name}\n"
        f"Signature: {user_name}\n\n"
        f"Email actuel a reecrire:\n"
        f"Objet: {subject}\n"
        f"Corps:\n{body}"
    )
    try:
        response = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=700,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        return {
            "subject": data.get("subject") or subject,
            "body": data.get("body") or body,
        }
    except Exception as e:
        print(f"[Mail] rewrite style err: {e}")
        return {"subject": subject, "body": body}


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
                    res = sb.table("contacts").select("id, full_name, email, writing_style_key").eq("id", contact_id).limit(1).execute()
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
        # Recharge le contact pour writing_style_key a jour
        if main_contact.get("id") and get_supabase():
            try:
                fr = (
                    get_supabase()
                    .table("contacts")
                    .select("id, full_name, email, writing_style_key")
                    .eq("id", main_contact["id"])
                    .limit(1)
                    .execute()
                )
                if fr.data:
                    main_contact = fr.data[0]
                    print(f"[Mail] contact reload style={main_contact.get('writing_style_key')}")
            except Exception as e:
                print(f"[Mail] contact reload err: {e}")
        contact_label = main_contact.get("full_name") or (to_names[0] if to_names else "")
        update_progress(request_id, "contact_found", contact_label)

        to_email = main_contact.get("email") or ""
        to_name = contact_label

        # Résoudre le CC AVANT missing_content (pour le garder même sans corps)
        cc_email = None
        cc_name = None
        if cc_names:
            cc_resolution = resolve_contacts(cc_names, user_id)
            if cc_resolution["found"]:
                cc_email = cc_resolution["found"][0].get("email")
                cc_name = cc_resolution["found"][0].get("full_name") or cc_names[0]
            else:
                cc_name = cc_names[0]

        if not has_enough_content:
            out = {
                "success": False,
                "reason": "missing_content",
                "contact_id": main_contact.get("id"),
                "contact_name": contact_label,
                "to": to_email,
                "request_id": request_id
            }
            if cc_name:
                out["cc_name"] = cc_name
            if cc_email:
                out["cc"] = cc_email
            return out

        update_progress(request_id, "generating_mail", contact_label)

        style_block = ""
        # Toujours relire le contact juste avant le style (evite cache / ancienne key)
        if main_contact.get("id"):
            try:
                sb = get_supabase()
                if sb:
                    fr = (
                        sb.table("contacts")
                        .select("id, full_name, email, writing_style_key")
                        .eq("id", main_contact["id"])
                        .limit(1)
                        .execute()
                    )
                    if fr.data:
                        main_contact = {**main_contact, **fr.data[0]}
            except Exception as e:
                print(f"[Mail] style refresh contact err: {e}")

        style_key = (
            (payload.get("style_key") or payload.get("writing_style_key") or "")
            or (main_contact.get("writing_style_key") if main_contact else "")
            or ""
        )
        if isinstance(style_key, str):
            style_key = style_key.strip().lower() or None
        else:
            style_key = None

        style = None
        if style_key and user_id:
            style = get_style(user_id, style_key=style_key)
            if not style:
                # retry sans filtre strict (au cas ou key differente casse)
                try:
                    sb = get_supabase()
                    if sb:
                        r = (
                            sb.table("user_writing_styles")
                            .select("*")
                            .eq("user_id", user_id)
                            .ilike("key", style_key)
                            .limit(1)
                            .execute()
                        )
                        style = (r.data or [None])[0]
                except Exception as e:
                    print(f"[Mail] style ilike err: {e}")
            style_block = style_prompt_block(style)
            ex = (style or {}).get("example_message") or ""
            print(
                f"[Mail] style key={style_key} loaded={bool(style)} "
                f"ex={ex[:60]!r} block_len={len(style_block)}"
            )
        else:
            print(
                f"[Mail] no style_key contact_id={main_contact.get('id')} "
                f"raw={main_contact.get('writing_style_key')!r} user={user_id}"
            )

        # Si un style contact est present, il ecrase ton/relation de l'intent
        email = await write_email(
            content_summary=content_summary,
            user_name=user_name,
            to_name=to_name,
            relationship=None if style_block else relationship,
            tone=None if style_block else tone,
            style_block=style_block,
        )

        # 2e passe si style charge (le 1er jet ignore souvent le vouvoiement)
        if style and style_block:
            email = await rewrite_email_to_style(
                subject=email.get("subject") or "",
                body=email.get("body") or "",
                style=style,
                user_name=user_name,
                to_name=to_name,
            )
            print(f"[Mail] style rewrite done key={style_key}")

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
