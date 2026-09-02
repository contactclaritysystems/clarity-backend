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
]

COMING_SOON = [
    {
        "key": "devis",
        "label": "Devis",
        "desc": "Préparer un chiffrage rapide à partir d'une simple demande orale.",
    },
    {
        "key": "factures",
        "label": "Facturation",
        "desc": "Créer, suivre et envoyer vos factures directement depuis Clarity.",
    },
    {
        "key": "documents",
        "label": "Documents",
        "desc": "Retrouver et exploiter vos documents sans avoir à les chercher vous-même.",
    },
    {
        "key": "ocr",
        "label": "Lecture automatique de documents",
        "desc": "Prendre une photo d'une facture ou d'un bon de commande, Clarity en extrait les informations utiles.",
    },
    {
        "key": "crm",
        "label": "Contacts avancés",
        "desc": "Centraliser toutes les informations de vos clients au même endroit.",
    },
    {
        "key": "taches",
        "label": "Tâches",
        "desc": "Suivre ce qu'il reste à faire, tout à la voix (au-delà des rappels déjà disponibles).",
    },
    {
        "key": "recherche",
        "label": "Recherche et résumés",
        "desc": "Retrouver rapidement une information ou obtenir une synthèse.",
    },
    {
        "key": "chantier",
        "label": "Compagnon de chantier",
        "desc": "Avant une intervention, Clarity vous rappelle le matériel, les documents et les points importants.",
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
