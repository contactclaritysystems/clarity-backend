"""
Agent Rédaction Clarity — premium
1) Demande vague → formulaire simple (champs selon le type)
2) Réponses du formulaire → rédaction factuelle, sans inventer
"""

import json
import os
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


# ---------------------------------------------------------------------------
# Formulaires selon le type de demande
# ---------------------------------------------------------------------------

FORMS = {
    "offre": {
        "title": "Votre offre",
        "submit_label": "Rédiger l'offre",
        "help": (
            "Remplissez seulement ce que vous savez.\n"
            "• Type : essai gratuit, promo, nouveau tarif…\n"
            "• Avantage : ex. 7 jours gratuits, -20 %\n"
            "• Date de fin : si vous en avez une\n"
            "• Pour qui : patrons, artisans, clients…\n"
            "Laissez vide ce que vous n'avez pas — Clarity n'inventera rien."
        ),
        "fields": [
            {"id": "type_offre", "label": "Type d'offre", "placeholder": "Ex : essai gratuit, promo rentrée", "required": False},
            {"id": "avantage", "label": "Avantage principal", "placeholder": "Ex : 7 jours gratuits", "required": True},
            {"id": "prix", "label": "Prix (si besoin)", "placeholder": "Ex : 29 € / mois", "required": False},
            {"id": "date_fin", "label": "Date de fin", "placeholder": "Ex : 30 septembre", "required": False},
            {"id": "public", "label": "Pour qui", "placeholder": "Ex : dirigeants, artisans", "required": False},
            {"id": "longueur", "label": "Longueur", "placeholder": "court ou un peu plus long", "required": False},
        ],
    },
    "post": {
        "title": "Votre post",
        "submit_label": "Rédiger le post",
        "help": (
            "Dites l'essentiel en mots simples.\n"
            "• Sujet : de quoi parle le post\n"
            "• Message clé : la phrase importante\n"
            "• But : annoncer, donner envie de s'inscrire…\n"
            "• Longueur : court (réseaux) ou un peu plus long\n"
            "Pas besoin de tout remplir."
        ),
        "fields": [
            {"id": "sujet", "label": "Sujet", "placeholder": "Ex : offre de rentrée", "required": True},
            {"id": "message_cle", "label": "Message clé", "placeholder": "Ex : 7 jours gratuits", "required": False},
            {"id": "but", "label": "But du post", "placeholder": "Ex : annoncer, faire s'inscrire", "required": False},
            {"id": "longueur", "label": "Longueur", "placeholder": "court ou un peu plus long", "required": False},
            {"id": "details", "label": "Autres détails", "placeholder": "Optionnel", "required": False},
        ],
    },
    "compte_rendu": {
        "title": "Compte-rendu",
        "submit_label": "Rédiger le compte-rendu",
        "help": (
            "Notez les points utiles.\n"
            "• Sujet de la réunion\n"
            "• Ce qui a été dit / décidé\n"
            "• Prochaines étapes si vous en avez\n"
            "Clarity ne rajoutera rien d'inventé."
        ),
        "fields": [
            {"id": "sujet", "label": "Sujet", "placeholder": "Ex : point devis avec Antoine", "required": True},
            {"id": "points", "label": "Points importants", "placeholder": "Ce qui s'est dit", "required": True},
            {"id": "decisions", "label": "Décisions", "placeholder": "Optionnel", "required": False},
            {"id": "suite", "label": "À faire ensuite", "placeholder": "Optionnel", "required": False},
        ],
    },
    "message": {
        "title": "Votre message",
        "submit_label": "Rédiger le message",
        "help": (
            "Indiquez pour qui et quoi dire.\n"
            "• Type de personne : frère, patron, ami… (ou créez-en un)\n"
            "• Ce qu'il faut transmettre\n"
            "Le style d'écriture vient de vos réglages / exemples."
        ),
        "fields": [
            {
                "id": "style_key",
                "label": "Type de personne",
                "placeholder": "frère, ami, patron…",
                "required": False,
                "field_type": "style_select",
            },
            {"id": "destinataire", "label": "Pour qui (nom)", "placeholder": "Ex : Antoine", "required": False},
            {"id": "contenu", "label": "Quoi dire", "placeholder": "L'essentiel du message", "required": True},
            {"id": "ton", "label": "Ton (optionnel)", "placeholder": "si différent du style", "required": False},
        ],
    },
    "generic": {
        "title": "Rédaction",
        "submit_label": "Rédiger",
        "help": (
            "Décrivez en mots simples ce que vous voulez.\n"
            "Plus vous précisez, plus le texte sera juste.\n"
            "Clarity n'invente pas ce que vous n'avez pas écrit."
        ),
        "fields": [
            {"id": "sujet", "label": "Sujet", "placeholder": "De quoi s'agit-il ?", "required": True},
            {"id": "details", "label": "Détails", "placeholder": "Points à inclure", "required": False},
            {"id": "ton", "label": "Ton / longueur", "placeholder": "pro, court…", "required": False},
        ],
    },
}


def detect_form_type(instruction: str) -> str:
    t = (instruction or "").lower()
    if any(w in t for w in ("compte-rendu", "compte rendu", "cr de", "cr d'", "réunion", "reunion")):
        return "compte_rendu"
    if any(w in t for w in ("post", "linkedin", "instagram", "insta", "story", "stories", "réseaux", "reseaux", "facebook", "tiktok")):
        return "post"
    if any(w in t for w in ("offre", "promo", "promotion", "essai gratuit", "rentrée", "rentree")):
        return "offre"
    if any(w in t for w in ("message", "sms", "texte pour", "écris à mon", "ecris a mon")):
        return "message"
    return "generic"


def form_response(form_type: str, request_id, instruction: str = "") -> dict:
    form = FORMS.get(form_type) or FORMS["generic"]
    brief = {
        "offre": "Offre — à compléter",
        "post": "Post — à compléter",
        "compte_rendu": "Compte-rendu — à compléter",
        "message": "Message — à compléter",
        "generic": "Rédaction — à compléter",
    }.get(form_type, "Rédaction — à compléter")
    return {
        "success": False,
        "reason": "needs_form",
        "ui": "form",
        "form_type": form_type,
        "title": form["title"],
        "submit_label": form["submit_label"],
        "help": form["help"],
        "fields": form["fields"],
        "brief": brief,
        "message": form["title"],
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
    }
    parts = []
    for k, v in answers.items():
        v = (v or "").strip()
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

MISSION : transformer des notes orales brutes en un compte-rendu professionnel.
Tu reformules chaque point en phrase claire (sujet + verbe).
Tu n'inventes AUCUN matériel, montant, date, nom ou décision absent des notes.

STRUCTURE (choisis celle qui colle au contenu, tu peux en mélanger 2) :
A) Chantier / devis / intervention
   - Contexte (sujet, lieu, contact s'ils sont dans le brief)
   - Matériel à prévoir (achats, fournitures)
   - Travaux à réaliser
   - Points de vigilance (seulement s'ils sont dans les notes)
B) Réunion / rendez-vous client
   - Contexte
   - Points abordés
   - Décisions
   - Suite à donner (seulement si dite)

Règles :
- Titre court en haut.
- Sections avec un titre, puis puces rédigées (pas "1. prévoir toles").
- Ex : "prévoir remplacement de 10 tôles" → "Prévoir le remplacement de 10 tôles."
- Pas de "Voici le compte-rendu".
- Tutoiement ou vouvoiement selon le brief ; par défaut vouvoiement neutre.
- Si un seul type de points : une seule famille de sections, pas les 6 vides.
"""


def is_compte_rendu(instruction: str, answers: dict | None = None) -> bool:
    t = (instruction or "").lower()
    if "compte-rendu" in t or "compte rendu" in t or t.startswith("rédige un compte"):
        return True
    if answers and (answers.get("points") or answers.get("points_dictes")):
        return True
    return False

WRITE_SYSTEM = """Tu es le rédacteur de Clarity Systems (SaaS français premium).

RÈGLE D'OR : zéro invention de CONTENU.
- Tu peux ajouter une forme minimale : Salut / Bonjour / À plus / Cordialement.
- Tu ne peux PAS ajouter d'idées absentes du brief
  (ex. "ça va être sympa", "j'ai hâte", "prépare-toi", excuses, motifs,
  bénéfices, emojis enthousiastes non demandés).

Ex brief : "Pour qui: mon frère · Contenu: je te récupère demain 8h · Ton: simple"
✅ "Salut, je te récupère demain à 8h."
✅ "Salut ! Je passe te prendre demain à 8h."
❌ "Salut ! Je te récupère demain à 8h. Prépare-toi, ça va être sympa ! À bientôt !"

Ex pro absence : seulement ce qui est dans le brief (maladie, etc.) + formules de politesse classiques.

Post / offre / CR : uniquement les faits du brief, formulés proprement.

Texte final direct, sans "Voici le message…".
"""


async def write_text(instruction: str, history: str, user_name: str, brief: str, memory_text: str = "", style_block: str = "", compte_rendu: bool = False) -> str:
    today = datetime.now().strftime("%d/%m/%Y")
    user_msg = f"Date : {today}\n"
    if user_name:
        user_msg += f"Auteur possible : {user_name}\n"
    if memory_text:
        user_msg += f"\n=== MÉMOIRE (ne l'utilise que si cohérent avec le brief) ===\n{memory_text}\n"
    if style_block:
        user_msg += f"\n=== STYLE UTILISATEUR ===\n{style_block}\n"
    if brief:
        user_msg += f"\n=== BRIEF (FAITS AUTORISÉS) ===\n{brief}\n"
    if history:
        user_msg += f"\n=== HISTORIQUE ===\n{history}\n"
    user_msg += f"\n=== DEMANDE ===\n{instruction}\n"
    if compte_rendu:
        user_msg += ("\nRédige le compte-rendu structuré. Reformule les points oraux. N'invente rien.")
    else:
        user_msg += ("\nRédige le message. Forme OK (Salut/Bonjour), mais AUCUNE idée en plus du brief.")

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


def has_enough_in_instruction(instruction: str) -> bool:
    """Demande déjà assez riche → pas de formulaire."""
    t = (instruction or "").lower()
    if is_compte_rendu(instruction) and len(instruction) > 80:
        return True
    if len(t) < 50:
        return False
    signals = sum(
        1
        for w in (
            "gratuit", "€", "euro", "jour", "mois", "prix", "essai",
            "agent", "patron", "admin", "%", "réunion", "reunion",
            "décision", "decision", "client",
        )
        if w in t
    )
    return signals >= 2


async def run_redaction_agent(payload: dict) -> dict:
    instruction = (payload.get("instruction") or "").strip()
    request_id = payload.get("request_id")
    user_name = (payload.get("user_name") or "").strip()
    history = payload.get("conversation_history") or ""
    user_id = payload.get("user_id")

    # Réponses du formulaire frontend
    form_answers = payload.get("form_answers") or payload.get("answers") or {}
    if isinstance(form_answers, str):
        try:
            form_answers = json.loads(form_answers)
        except Exception:
            form_answers = {}
    print(f"[Redaction] form_answers keys={list(form_answers.keys()) if isinstance(form_answers, dict) else type(form_answers)} payload_keys={list(payload.keys())}")

    if not instruction and not form_answers:
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

        # --- Formulaire déjà rempli → rédaction ---
        if form_answers and isinstance(form_answers, dict) and any(
            (v or "").strip() for v in form_answers.values() if isinstance(v, str)
        ):
            brief = answers_to_brief(form_answers)
            base_instruction = instruction or payload.get("original_instruction") or "Rédige le texte demandé."
            sk = (
                form_answers.get("style_key")
                or payload.get("style_key")
                or ""
            ).strip()
            sid = (payload.get("style_id") or form_answers.get("style_id") or "").strip()
            style = get_style(user_id, style_key=sk or None, style_id=sid or None) if user_id else None
            style_block = style_prompt_block(style)
            text_out = await write_text(
                base_instruction,
                history,
                user_name,
                brief=brief,
                memory_text=memory_text,
                style_block=style_block,
                compte_rendu=is_compte_rendu(base_instruction, form_answers),
            )
            title_out = "Compte-rendu" if is_compte_rendu(base_instruction, form_answers) else "Rédaction"
            if not text_out:
                return {
                    "success": False,
                    "message": "Je n'ai pas pu générer le texte.",
                    "request_id": request_id,
                }
            await extract_and_save_memory(user_id, base_instruction, text_out, history + "\n" + brief)
            return {
                "success": True,
                "title": title_out,
                "message": text_out,
                "content": text_out,
                "brief": brief,
                "request_id": request_id,
            }

        # --- Reformulation ---
        if is_rewrite_request(instruction) and history:
            text_out = await write_text(
                instruction, history, user_name, brief="", memory_text=memory_text
            )
            if not text_out:
                text_out = "Je n'ai pas pu reformuler."
            await extract_and_save_memory(user_id, instruction, text_out, history)
            return {
                "success": True,
                "title": "Rédaction",
                "message": text_out,
                "content": text_out,
                "request_id": request_id,
            }

        # --- Déjà assez d'infos dans la phrase ---
        if has_enough_in_instruction(instruction):
            text_out = await write_text(
                instruction, history, user_name, brief=instruction, memory_text=memory_text
            )
            if not text_out:
                return {
                    "success": False,
                    "message": "Je n'ai pas pu générer le texte.",
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

        # --- Sinon : formulaire adapté ---
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
        return {
            "success": False,
            "message": f"Erreur rédaction : {e}",
            "request_id": request_id,
        }
