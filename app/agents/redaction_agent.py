"""
Agent Rédaction Clarity — premium
"""

import json
import os
import re
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv
from app.memory import load_memory, memory_as_text, extract_and_save_memory
from app.writing_styles import get_style, style_prompt_block, list_styles

load_dotenv()
MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")


def get_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY manquante")
    return OpenAI(api_key=api_key)


FORMS = {
    "offre": {
        "title": "Votre offre",
        "submit_label": "Rédiger l'offre",
        "help": "Remplissez seulement ce que vous savez. Clarity n'inventera rien.",
        "fields": [
            {"id": "type_offre", "label": "Type d'offre", "placeholder": "Ex : essai gratuit", "required": False},
            {"id": "avantage", "label": "Avantage principal", "placeholder": "Ex : 7 jours gratuits", "required": True},
            {"id": "prix", "label": "Prix (si besoin)", "placeholder": "Ex : 29 ¬ / mois", "required": False},
            {"id": "date_fin", "label": "Date de fin", "placeholder": "Ex : 30 septembre", "required": False},
            {"id": "public", "label": "Pour qui", "placeholder": "Ex : artisans", "required": False},
            {"id": "longueur", "label": "Longueur", "placeholder": "court ou un peu plus long", "required": False},
        ],
    },
    "post": {
        "title": "Votre post",
        "submit_label": "Rédiger le post",
        "help": "Dites l'essentiel en mots simples.",
        "fields": [
            {"id": "sujet", "label": "Sujet", "placeholder": "Ex : offre de rentrée", "required": True},
            {"id": "message_cle", "label": "Message clé", "placeholder": "Ex : 7 jours gratuits", "required": False},
            {"id": "but", "label": "But du post", "placeholder": "Ex : annoncer", "required": False},
            {"id": "longueur", "label": "Longueur", "placeholder": "court ou un peu plus long", "required": False},
            {"id": "details", "label": "Autres détails", "placeholder": "Optionnel", "required": False},
        ],
    },
    "compte_rendu": {
        "title": "Compte-rendu",
        "submit_label": "Rédiger le compte-rendu",
        "help": "Notez les points utiles. Clarity ne rajoutera rien d'inventé.",
        "fields": [
            {"id": "sujet", "label": "Sujet", "placeholder": "Ex : point devis", "required": True},
            {"id": "points", "label": "Points importants", "placeholder": "Ce qui s'est dit", "required": True},
            {"id": "decisions", "label": "Décisions", "placeholder": "Optionnel", "required": False},
            {"id": "suite", "label": "À faire ensuite", "placeholder": "Optionnel", "required": False},
        ],
    },
    "message": {
        "title": "Votre message",
        "submit_label": "Rédiger le message",
        "help": "Indiquez pour qui et quoi dire.",
        "fields": [
            {
                "id": "style_key",
                "label": "Type de personne",
                "placeholder": "frère, ami, patron&",
                "required": False,
                "field_type": "style_select",
            },
            {"id": "destinataire", "label": "Pour qui (nom)", "placeholder": "Ex : Antoine", "required": False},
            {"id": "contenu", "label": "Quoi dire", "placeholder": "L'essentiel", "required": True},
            {"id": "ton", "label": "Ton (optionnel)", "placeholder": "si différent du style", "required": False},
        ],
    },
    "generic": {
        "title": "Rédaction",
        "submit_label": "Rédiger",
        "help": "Décrivez en mots simples. Clarity n'invente pas.",
        "fields": [
            {"id": "sujet", "label": "Sujet", "placeholder": "De quoi s'agit-il ?", "required": True},
            {"id": "details", "label": "Détails", "placeholder": "Points à inclure", "required": False},
            {"id": "ton", "label": "Ton / longueur", "placeholder": "pro, court&", "required": False},
        ],
    },
}


def detect_form_type(instruction: str) -> str:
    t = (instruction or "").lower()
    if is_compte_rendu(instruction):
        return "compte_rendu"
    if any(k in t for k in ("offre", "promo", "essai gratuit", "tarif")):
        return "offre"
    if any(k in t for k in ("post", "linkedin", "insta", "instagram", "facebook", "réseau")):
        return "post"
    if any(k in t for k in ("message", "sms", "texto", "whatsapp")):
        return "message"
    return "generic"


def form_response(form_type: str, request_id, instruction: str = "") -> dict:
    spec = FORMS.get(form_type) or FORMS["generic"]
    return {
        "success": False,
        "reason": "needs_form",
        "title": spec["title"],
        "message": spec.get("help") or "",
        "submit_label": spec.get("submit_label") or "Rédiger",
        "fields": spec["fields"],
        "help": spec.get("help") or "",
        "form_type": form_type,
        "original_instruction": instruction,
        "request_id": request_id,
    }


def answers_to_brief(answers: dict) -> str:
    if not answers:
        return ""
    labels = {
        "type_offre": "Type",
        "avantage": "Avantage",
        "prix": "Prix",
        "date_fin": "Date de fin",
        "public": "Public",
        "longueur": "Longueur",
        "sujet": "Sujet",
        "message_cle": "Message clé",
        "but": "But",
        "details": "Détails",
        "points": "Points",
        "decisions": "Décisions",
        "suite": "Suite",
        "destinataire": "Destinataire",
        "contenu": "Contenu",
        "ton": "Ton",
        "adresse": "Adresse / lieu",
        "contact": "Contact",
        "societe": "Société",
        "points_dictes": "Points dictés",
        "photo_urls": "Photos",
        "tranchee_metres": "Longueur tranchée",
        "longueur_portail": "Longueur portail",
        "pieces_a_reparer": "Pièces à réparer",
    }
    label_by_id = {}
    for it in answers.get("checklist_items") or []:
        if isinstance(it, dict) and it.get("id"):
            label_by_id[str(it["id"])] = it.get("label") or it["id"]
    parts = []
    raw_chk = answers.get("checklist_answers")
    if isinstance(raw_chk, dict):
        oui, non, textes = [], [], []
        for kid, val in raw_chk.items():
            lab = label_by_id.get(str(kid), labels.get(str(kid), str(kid)))
            lv = str(val).lower().strip()
            if lv in ("oui", "yes", "true", "1"):
                oui.append(lab)
            elif lv in ("non", "no", "false", "0", "passer", ""):
                if lv in ("non", "no", "false", "0"):
                    non.append(lab)
            else:
                textes.append(f"{lab} : {val}")
        if oui:
            parts.append("Confirmé : " + " ; ".join(oui))
        if non:
            parts.append("Écarté : " + " ; ".join(non))
        parts.extend(textes)
    for k, v in answers.items():
        if k in ("checklist_answers", "checklist_items", "photos", "checklist_done"):
            continue
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v if x)
        elif isinstance(v, dict):
            continue
        else:
            v = str(v or "").strip()
        if not v:
            continue
        parts.append(f"{labels.get(k, k)} : {v}")
    return " · ".join(parts)


def is_rewrite_request(instruction: str) -> bool:
    t = (instruction or "").lower().strip()
    keys = [
        "plus court", "plus long", "plus pro", "plus professionnel",
        "plus chaleureux", "plus formel", "reformule", "réécris", "reecris",
        "autre version", "change le ton", "raccourci", "allonge",
    ]
    return any(k in t for k in keys)


CR_WRITE_SYSTEM = """Tu es le rédacteur de comptes-rendus de Clarity Systems.

Tu n'utilises QUE le brief et la demande de CE tour.
INTERDIT : ressortir un ancien chantier (cotes, kg, nombre de toles, couleur)
si ce n'est pas ecrit dans le brief.
INTERDIT d'inventer un diametre, un poids, une couleur, une quantite.

Chantier : deux blocs seulement s'ils ont du contenu dans le brief :
- Matériel à prévoir : pièces, consommables, moteur, peinture CITÉS (sans inventer une qté)
- Travaux à réaliser : actions (poser, poncer, peindre, remplacer)
Si un élément est à la fois acheté et posé, il peut être dans les deux (tole = matériel + remplacement).
Réunion : seulement si le brief parle d'une réunion. JAMAIS chantier + réunion ensemble.
Pas de phrase "aucun autre", "aucune décision", "aucune suite".
Titre court. Pas de "Voici le compte-rendu".
"""

WRITE_SYSTEM = """Tu es le rédacteur de Clarity Systems (SaaS français premium).
RÈGLE D'OR : zéro invention de CONTENU.
Texte final direct, sans "Voici le message&".
"""


async def write_text(instruction: str, history: str, user_name: str, brief: str, memory_text: str = "", style_block: str = "", compte_rendu: bool = False) -> str:
    today = datetime.now().strftime("%d/%m/%Y")
    user_msg = f"Date : {today}\n"
    if user_name:
        user_msg += f"Auteur possible : {user_name}\n"
    if memory_text:
        user_msg += f"\n=== MÉMOIRE ===\n{memory_text}\n"
    if style_block:
        user_msg += f"\n=== STYLE UTILISATEUR ===\n{style_block}\n"
    if brief:
        user_msg += f"\n=== BRIEF (FAITS AUTORISÉS) ===\n{brief}\n"
    if history:
        user_msg += f"\n=== HISTORIQUE ===\n{history}\n"
    user_msg += f"\n=== DEMANDE ===\n{instruction}\n"
    if compte_rendu:
        user_msg += "\nRédige le compte-rendu structuré. Reformule. N'invente rien."
    else:
        user_msg += "\nRédige le message. Aucune idée en plus du brief."
    response = get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": CR_WRITE_SYSTEM if compte_rendu else WRITE_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.35,
        max_tokens=1600 if compte_rendu else 1200,
    )
    return (response.choices[0].message.content or "").strip()


def is_compte_rendu(instruction: str, answers: dict | None = None) -> bool:
    t = (instruction or "").lower()
    if "compte-rendu" in t or "compte rendu" in t or t.startswith("rédige un compte"):
        return True
    if answers and (answers.get("points") or answers.get("points_dictes")):
        return True
    return False


def has_enough_in_instruction(instruction: str) -> bool:
    t = (instruction or "").lower()
    if is_compte_rendu(instruction) and len(instruction) > 80:
        return True
    if len(t) < 50:
        return False
    signals = sum(
        1
        for w in ("gratuit", "¬", "euro", "jour", "mois", "prix", "essai", "réunion", "reunion", "décision", "client")
        if w in t
    )
    return signals >= 2


def _already_said_blob(instruction: str, answers: dict) -> str:
    bits = [instruction or ""]
    if isinstance(answers, dict):
        for k in ("sujet", "points", "points_dictes", "details", "decisions"):
            bits.append(str(answers.get(k) or ""))
    return " ".join(bits).lower()


def _tokens(text: str) -> set:
    stop = {
        "le", "la", "les", "un", "une", "des", "de", "du", "et", "ou", "a", "à",
        "au", "aux", "en", "pour", "pas", "est", "sont", "avec", "sur", "dans",
    }
    words = re.findall(r"[a-zàâäéèêëïîôùûüç0-9]+", (text or "").lower())
    return {w for w in words if len(w) >= 4 and w not in stop}


def _overlaps_said(label: str, said: set) -> bool:
    toks = _tokens(label)
    if not toks or not said:
        return False
    return len(toks & said) >= 2 or any(w in said and len(w) >= 5 for w in toks)


PACKS = {
    "portail": [
        {
            "id": "type_portail",
            "label": "Coulissant ou battant ?",
            "type": "texte",
            "placeholder": "coulissant / battant",
        },
        {
            "id": "moteur",
            "label": "Y a-t-il un moteur ?",
            "type": "oui_non",
            "followups_if_oui": [
                {"id": "moteur_existant", "label": "Faut-il déposer le moteur actuel ?", "type": "oui_non"},
                {
                    "id": "longueur_portail",
                    "label": "Longueur du vantail (crémaillère) ?",
                    "type": "texte",
                    "placeholder": "Ex : 4 m",
                },
            ],
        },
        {
            "id": "poncage_peinture",
            "label": "Faut-il poncer et peindre ?",
            "type": "oui_non",
        },
        {
            "id": "elements",
            "label": "Des éléments à changer ou à ajouter ?",
            "type": "oui_non",
            "followups_if_oui": [
                {
                    "id": "elements_liste",
                    "label": "Lesquels ?",
                    "type": "texte",
                    "placeholder": "Ex : tôles, galets, moteur",
                },
            ],
        },
    ],
    "sdb": [
        {"id": "depose", "label": "Faut-il déposer l'existant ?", "type": "oui_non"},
        {
            "id": "depose_quoi",
            "label": "Dépose : douche, baignoire, lavabo, bidet, carrelage ?",
            "type": "texte",
            "placeholder": "Ex : baignoire + carrelage",
        },
        {
            "id": "repose_quoi",
            "label": "Pose : douche, baignoire, lavabo, carrelage ?",
            "type": "texte",
            "placeholder": "Ex : douche + carrelage",
        },
        {"id": "colle_carreaux", "label": "Prévoir colle et carreaux ?", "type": "oui_non"},
        {"id": "seche_serviette", "label": "Sèche-serviettes à prévoir ?", "type": "oui_non"},
    ],
    "peinture": [
        {"id": "inter_exter", "label": "Intérieur ou extérieur ?", "type": "texte", "placeholder": "intérieur / extérieur"},
        {"id": "poncage", "label": "Faut-il poncer ?", "type": "oui_non"},
        {"id": "sous_couche", "label": "Sous-couche à prévoir ?", "type": "oui_non"},
        {"id": "nombre_couches", "label": "Combien de couches ?", "type": "texte", "placeholder": "Ex : 2"},
    ],
    "reunion": [
        {"id": "decision", "label": "Une décision a-t-elle été prise ?", "type": "oui_non"},
        {"id": "relance", "label": "Faut-il rappeler quelqu'un ?", "type": "oui_non"},
        {"id": "doc", "label": "Un document à envoyer ?", "type": "oui_non"},
        {"id": "suite", "label": "Une date de suite ?", "type": "oui_non"},
    ],
}


def _detect_cr_family(blob: str) -> str | None:
    t = (blob or "").lower()
    if any(k in t for k in ("portail", "portillon", "cloture", "clôture", "coulissant", "battant")):
        return "portail"
    if any(k in t for k in ("salle de bain", "sdb", "douche", "baignoire", "lavabo", "carrelage")):
        return "sdb"
    if any(k in t for k in ("peinture", "facade", "façade", "enduit", "ponçage", "poncage")):
        return "peinture"
    if any(k in t for k in ("réunion", "reunion", "rdv", "rendez-vous", "visite client", "point client")):
        return "reunion"
    return None


def _prune_item(it: dict, said: set) -> dict | None:
    if _overlaps_said(it.get("label") or "", said):
        return None
    row = {
        "id": it["id"],
        "label": it["label"],
        "type": it.get("type") or it.get("field_type") or "oui_non",
    }
    if it.get("placeholder"):
        row["placeholder"] = it["placeholder"]
    if it.get("hint"):
        row["hint"] = it["hint"]
    oui = [_prune_item(k, said) for k in (it.get("followups_if_oui") or [])]
    non = [_prune_item(k, said) for k in (it.get("followups_if_non") or [])]
    oui = [k for k in oui if k]
    non = [k for k in non if k]
    if oui:
        row["followups_if_oui"] = oui
    if non:
        row["followups_if_non"] = non
    return row


def generate_cr_checklist(instruction: str, answers: dict) -> list:
    blob = _already_said_blob(instruction, answers)
    kind = _detect_cr_family(blob)
    if not kind:
        return []
    raw = PACKS.get(kind) or []
    said = _tokens(blob)
    items = []
    for it in raw:
        pruned = _prune_item(it, said)
        if pruned:
            items.append(pruned)
    return items


def _checklist_done(form_answers: dict) -> bool:
    if not isinstance(form_answers, dict):
        return False
    if "checklist_answers" in form_answers:
        return True
    return bool(form_answers.get("checklist_done"))


async def run_redaction_agent(payload: dict) -> dict:
    instruction = (payload.get("instruction") or "").strip()
    request_id = payload.get("request_id")
    user_name = (payload.get("user_name") or "").strip()
    history = payload.get("conversation_history") or ""
    user_id = payload.get("user_id")

    form_answers = payload.get("form_answers") or payload.get("answers") or {}
    if isinstance(form_answers, str):
        try:
            form_answers = json.loads(form_answers)
        except Exception:
            form_answers = {}
    print(f"[Redaction] keys={list(form_answers.keys()) if isinstance(form_answers, dict) else type(form_answers)}")

    if not instruction and not form_answers:
        return {
            "success": False,
            "reason": "missing_content",
            "message": "Que souhaitez-vous que je rédige ?",
            "request_id": request_id,
        }

    try:
        facts = load_memory(user_id)
        memory_text = memory_as_text(facts)

        if form_answers and isinstance(form_answers, dict) and (
            any((isinstance(v, str) and v.strip()) for v in form_answers.values())
            or form_answers.get("checklist_answers") is not None
        ):
            brief = answers_to_brief(form_answers)
            base_instruction = instruction or payload.get("original_instruction") or "Rédige le texte demandé."
            cr = is_compte_rendu(base_instruction, form_answers)
            if cr and not _checklist_done(form_answers):
                items = generate_cr_checklist(base_instruction, form_answers)
                return {
                    "success": False,
                    "reason": "needs_checklist",
                    "ui": "checklist",
                    "title": "Pour ne rien oublier",
                    "message": "Répondez seulement à ce qui est utile.",
                    "items": items,
                    "photos_allowed": True,
                    "max_photos": 8,
                    "form_answers": form_answers,
                    "original_instruction": base_instruction,
                    "request_id": request_id,
                }
            sk = (form_answers.get("style_key") or payload.get("style_key") or "").strip()
            sid = (payload.get("style_id") or form_answers.get("style_id") or "").strip()
            style = get_style(user_id, style_key=sk or None, style_id=sid or None) if user_id else None
            text_out = await write_text(
                base_instruction,
                "" if cr else history,
                user_name,
                brief=brief,
                memory_text="" if cr else memory_text,
                style_block="" if cr else style_prompt_block(style),
                compte_rendu=cr,
            )
            if not text_out:
                return {"success": False, "message": "Je n'ai pas pu générer le texte.", "request_id": request_id}
            if not cr:
                await extract_and_save_memory(user_id, base_instruction, text_out, history + "\n" + brief)
            photos = form_answers.get("photo_urls") or form_answers.get("photos") or []
            if isinstance(photos, str):
                photos = [photos]
            return {
                "success": True,
                "title": "Compte-rendu" if cr else "Rédaction",
                "message": text_out,
                "content": text_out,
                "brief": brief,
                "photo_urls": [u for u in photos if u],
                "request_id": request_id,
            }

        if is_rewrite_request(instruction) and history:
            text_out = await write_text(instruction, history, user_name, brief="", memory_text=memory_text)
            return {
                "success": True,
                "title": "Rédaction",
                "message": text_out or "Je n'ai pas pu reformuler.",
                "content": text_out or "Je n'ai pas pu reformuler.",
                "request_id": request_id,
            }

        if has_enough_in_instruction(instruction):
            if is_compte_rendu(instruction) and not _checklist_done(form_answers or {}):
                items = generate_cr_checklist(instruction, form_answers or {})
                return {
                    "success": False,
                    "reason": "needs_checklist",
                    "ui": "checklist",
                    "title": "Pour ne rien oublier",
                    "message": "Répondez seulement à ce qui est utile.",
                    "items": items,
                    "photos_allowed": True,
                    "max_photos": 8,
                    "form_answers": form_answers or {},
                    "original_instruction": instruction,
                    "request_id": request_id,
                }
            text_out = await write_text(
                instruction, history, user_name, brief=instruction, memory_text=memory_text,
                compte_rendu=is_compte_rendu(instruction),
            )
            return {
                "success": True,
                "title": "Rédaction",
                "message": text_out,
                "content": text_out,
                "request_id": request_id,
            }

        form_type = detect_form_type(instruction)
        resp = form_response(form_type, request_id, instruction=instruction)
        if user_id:
            try:
                resp["styles"] = list_styles(user_id)
            except Exception:
                resp["styles"] = []
        return resp
    except Exception as e:
        print(f"[Redaction] error: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "message": f"Erreur rédaction : {e}", "request_id": request_id}
