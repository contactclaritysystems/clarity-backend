"""Agent Tâches : créer une ligne à cocher (pas un rappel notifié)."""
from __future__ import annotations

import json
import os
import re
from typing import Optional
from openai import OpenAI
from dotenv import load_dotenv
from app.tasks import create_task

load_dotenv()
MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")


def get_client():
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


FILLER = re.compile(
    r"^(ajoute|ajouter|crée|creer|créer|note|noter|mets|mettre|nouvelle)\s+"
    r"(une\s+)?(tâche|tache|todo|to[- ]?do)\s*[:\-]?\s*",
    re.I,
)
FILLER2 = re.compile(
    r"^(à faire|a faire|il faut que je|je dois|note que je dois)\s*[:\-]?\s*",
    re.I,
)


def cheap_title(instruction: str) -> str:
    t = (instruction or "").strip()
    t = FILLER.sub("", t)
    t = FILLER2.sub("", t)
    return t.strip() or instruction.strip()


async def run_taches_agent(payload: dict) -> dict:
    instruction = (payload.get("instruction") or "").strip()
    user_id = payload.get("user_id") or ""
    request_id = payload.get("request_id")
    if not instruction:
        return {"success": False, "message": "Quelle tâche dois-je noter ?", "request_id": request_id}
    if not user_id:
        return {"success": False, "message": "Connexion requise.", "request_id": request_id}

    titre = cheap_title(instruction)
    due = None
    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extrais une tâche à cocher (PAS un rappel notifié).\n"
                        "JSON: {\"titre\": \"...\", \"due_date\": null ou \"YYYY-MM-DD\"}\n"
                        "titre = l'action, sans « ajoute une tâche ».\n"
                        "due_date seulement si une date est dite."
                    ),
                },
                {"role": "user", "content": instruction},
            ],
            temperature=0,
            max_tokens=120,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        titre = (data.get("titre") or titre).strip()
        due = data.get("due_date") or None
    except Exception as e:
        print(f"[Taches] extract: {e}")

    if not titre or len(titre) < 2:
        return {
            "success": False,
            "reason": "missing_title",
            "message": "Quelle tâche dois-je noter ?",
            "request_id": request_id,
        }
    titre = titre[0].upper() + titre[1:]
    created = create_task(user_id, titre, due)
    if not created.get("success"):
        return {
            "success": False,
            "message": created.get("message") or "Impossible d'enregistrer la tâche.",
            "request_id": request_id,
        }
    extra = f" pour le {due[8:10]}/{due[5:7]}" if due and len(str(due)) >= 10 else ""
    return {
        "success": True,
        "agent": "taches",
        "message": f"Tâche notée{extra} : {titre}",
        "task": created.get("task"),
        "request_id": request_id,
    }
