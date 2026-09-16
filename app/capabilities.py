"""
Capacités Clarity : disponible maintenant + bientôt.
Source unique pour orchestrateur, assistant, et API frontend.
"""

AVAILABLE = [
    {
        "key": "mail",
        "label": "Mails",
        "desc": "Rédiger et préparer un e-mail (aperçu avant envoi), avec le bon ton selon le contact.",
    },
    {
        "key": "planning",
        "label": "Rendez-vous",
        "desc": "Créer, modifier ou annuler un rendez-vous dans votre planning.",
    },
    {
        "key": "relance",
        "label": "Rappels",
        "desc": "Créer un rappel (« faites-moi penser à… ») à la date et l’heure choisies.",
    },
    {
        "key": "redaction",
        "label": "Rédaction",
        "desc": "Rédiger un texte, post, offre ou message sans envoi mail automatique.",
    },
    {
        "key": "assistant",
        "label": "Assistant",
        "desc": "Répondre à vos questions, résumer votre journée, retrouver un contact ou une info.",
    },
    {
        "key": "taches",
        "label": "Tâches",
        "desc": "Noter une tâche à faire, sans notification (liste Planning).",
    },
    {
        "key": "analyser",
        "label": "Analyser",
        "desc": "Joindre une photo ou un PDF (trombone ou bouton Analyser). Clarity lit le fichier, répond, ou l'envoie par mail.",
    },
    {
        "key": "compte_rendu",
        "label": "Compte rendu",
        "desc": "Dicter les points d'une visite ou d'une réunion, Clarity rédige le compte rendu.",
    },
]

COMING_SOON = [
    {
        "key": "sms",
        "label": "SMS",
        "desc": "Rédiger un SMS comme un mail. L'aperçu s'ouvre dans l'app Messages, vous validez l'envoi.",
    },
    {
        "key": "gps",
        "label": "Itinéraire",
        "desc": "« Ouvre le GPS pour aller chez Anthony » : Maps, Waze ou Plans, à partir de l'adresse du contact.",
    },
    {
        "key": "en_route",
        "label": "Prévenir en route",
        "desc": "Prévenir le client par SMS : j'arrive dans X minutes.",
    },
    {
        "key": "devis",
        "label": "Devis",
        "desc": "Préparer un chiffrage à partir d'une demande orale.",
    },
    {
        "key": "factures",
        "label": "Facturation",
        "desc": "Créer et envoyer vos factures depuis Clarity.",
    },
    {
        "key": "calendar",
        "label": "Agenda Google",
        "desc": "Voir dans Clarity les rendez-vous déjà dans votre agenda.",
    },
    {
        "key": "whatsapp",
        "label": "WhatsApp",
        "desc": "Même principe que le SMS, dans WhatsApp.",
    },
    {
        "key": "chantier",
        "label": "Compagnon de chantier",
        "desc": "Avant une intervention : matériel et points à ne pas oublier.",
    },
]


def capabilities_prompt_block() -> str:
    """Bloc texte pour les prompts LLM."""
    lines = ["CAPACITÉS DISPONIBLES MAINTENANT :"]
    for a in AVAILABLE:
        lines.append(f"- {a['label']} : {a['desc']}")
    lines.append("")
    lines.append("BIENTÔT (pas encore disponible — ne jamais prétendre que c’est déjà fait) :")
    for c in COMING_SOON:
        lines.append(f"- {c['label']} : {c['desc']}")
    lines.append("")
    lines.append(
        "Si la demande correspond à un item BIENTÔT : expliquer brièvement à quoi ça servira, "
        "dire que ce n’est pas encore disponible, proposer une alternative parmi les capacités actuelles. "
        "Ne pas inventer de date de livraison."
    )
    lines.append(
        "Si la demande est utile au produit mais hors scope (ni dispo ni bientôt clair) : "
        "répondre poliment, proposer les capacités actuelles, et indiquer que l’idée peut être transmise à l’équipe."
    )
    return "\n".join(lines)


def public_payload() -> dict:
    return {"available": AVAILABLE, "coming_soon": COMING_SOON}
