import os
"""
Clarity Systems - Backend natif
"""

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from app.orchestrator import run_orchestrator
from app.agents.mail_agent import run_mail_agent
from app.agents.planning_agent import run_planning_agent
from app.agents.relance_agent import run_relance_agent
from app.agents.assistant_agent import run_assistant_agent
from app.agents.redaction_agent import run_redaction_agent
from app.agents.taches_agent import run_taches_agent
from app.writing_styles import list_styles, create_style
from app.capabilities import public_payload
from app.feature_requests import save_feature_request
from app.notifications import run_notification_pass, send_email, get_digest_settings, save_digest_settings
from app.tasks import list_tasks, create_task, update_task, delete_task
from app.contact_activity import get_activity, patch_contact, log_sent_mail

_NOTIFY_TEST_LAST = {}

app = FastAPI(
    title="Clarity Backend",
    description="Remplacement natif des webhooks Make",
    version="0.2.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class OrchestratorPayload(BaseModel):
    request_id: Optional[str] = None
    user_email: Optional[str] = None
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    instruction: str
    conversation_history: Optional[str] = ""
    last_agent: Optional[str] = ""


class AgentPayload(BaseModel):
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    user_name: Optional[str] = None
    instruction: str = ""
    request_id: Optional[str] = None
    contact_id: Optional[str] = None
    # Planning / Relances (slot-filling)
    reason: Optional[str] = None
    contact_name: Optional[str] = None
    message_context: Optional[str] = None
    reminder_date: Optional[str] = None
    reminder_time: Optional[str] = None
    appointment_date: Optional[str] = None
    appointment_time: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    conversation_history: Optional[str] = None
    # Rédaction — formulaire
    form_answers: Optional[dict] = None
    answers: Optional[dict] = None
    original_instruction: Optional[str] = None
    style_key: Optional[str] = None
    style_id: Optional[str] = None


@app.get("/")
async def root():
    return {"status": "ok", "service": "Clarity Backend", "version": "0.2.0"}


@app.get("/health")
async def health():
    return {"status": "healthy"}



class NotifyTestPayload(BaseModel):
    user_id: Optional[str] = None
    user_email: Optional[str] = None


@app.post("/notify/test")
async def notify_test(payload: NotifyTestPayload):
    """Envoi immédiat d'un e-mail test (onboarding anti-spam)."""
    email = (payload.user_email or "").strip()
    if not email:
        return {"success": False, "message": "Adresse e-mail manquante."}
    import time as _time
    last = _NOTIFY_TEST_LAST.get(email.lower(), 0)
    if _time.time() - last < 600:
        return {
            "success": False,
            "message": "Un test a déjà été envoyé. Réessayez dans quelques minutes.",
        }
    _NOTIFY_TEST_LAST[email.lower()] = _time.time()
    result = send_email(
        email,
        "Test Clarity — vos rappels arriveront ici",
        "Ceci est un test.\n\n"
        "Si vous recevez ce message, les rappels et rendez-vous Clarity "
        "arriveront bien sur cette adresse.\n\n"
        "Si le mail est dans les indésirables : ouvrez-le et indiquez "
        "« Non, ce n'est pas un spam ».\n"
        "Vous pouvez aussi ajouter contact.claritysystems@gmail.com à vos contacts.\n\n"
        "— Clarity",
    )
    if result == "ok":
        return {
            "success": True,
            "message": f"E-mail test envoyé à {email}. Pensez aux indésirables si vous ne le voyez pas.",
        }
    return {"success": False, "message": "Impossible d'envoyer le test pour le moment."}



class DigestSettingsPayload(BaseModel):
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    digest_enabled: Optional[bool] = None
    digest_time: Optional[str] = None
    reminder_offset: Optional[str] = None
    appointment_offset: Optional[str] = None


@app.get("/notify/settings")
async def notify_settings_get(user_id: str = ""):
    if not user_id:
        return {"success": False, "message": "user_id manquant"}
    s = get_digest_settings(user_id)
    return {"success": True, "settings": s, **s}


@app.post("/notify/settings")
async def notify_settings_post(payload: DigestSettingsPayload):
    if not payload.user_id:
        return {"success": False, "message": "user_id manquant"}
    return save_digest_settings(payload.user_id, payload.digest_enabled, payload.digest_time, payload.user_email, payload.reminder_offset, payload.appointment_offset)


@app.get("/cron/notifications")
@app.post("/cron/notifications")
async def cron_notifications(request: Request):
    """Appelé toutes les 2 min (cron-job.org). Protégé par CRON_SECRET."""
    secret = os.getenv("CRON_SECRET") or ""
    q = request.query_params.get("secret") or ""
    auth = request.headers.get("authorization") or ""
    token = q
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
    if secret and token != secret:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    return run_notification_pass()






@app.get("/admin/telegram-test")
async def admin_telegram_test(request: Request):
    secret = os.getenv("CRON_SECRET") or ""
    q = request.query_params.get("secret") or ""
    if secret and q != secret:
        return JSONResponse({"ok": False}, status_code=401)
    from app.admin_alerts import telegram_send
    ok = telegram_send("🧪 Test Clarity Admin — le canal fonctionne.")
    return {"ok": ok}

@app.get("/capabilities")
async def capabilities_list():
    """Liste disponible + bientôt (pour le bouton ? / réglages)."""
    return public_payload()


class FeatureRequestPayload(BaseModel):
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    idea: str = ""
    source: Optional[str] = "help"


@app.post("/feature-request")
async def feature_request_endpoint(payload: FeatureRequestPayload):
    """Une idée, un besoin ? — enregistre pour l'équipe Clarity."""
    return save_feature_request(
        user_id=payload.user_id,
        user_email=payload.user_email,
        idea=payload.idea,
        source=payload.source or "help",
    )


@app.post("/orchestrator")
async def orchestrator_endpoint(payload: OrchestratorPayload):
    try:
        return await run_orchestrator(payload.model_dump())
    except Exception as e:
        print(f"[ORCHESTRATOR ERROR] {e}")
        return {
            "success": False,
            "message": "Erreur orchestrateur. Réessaie.",
            "request_id": payload.request_id
        }


@app.post("/agent/mail")
async def mail_agent_endpoint(payload: AgentPayload):
    try:
        return await run_mail_agent(payload.model_dump())
    except Exception as e:
        print(f"[MAIL AGENT ERROR] {e}")
        return {
            "success": False,
            "message": "Impossible de préparer le mail.",
            "request_id": payload.request_id
        }


@app.post("/agent/planning")
async def planning_agent_endpoint(payload: AgentPayload):
    try:
        return await run_planning_agent(payload.model_dump())
    except Exception as e:
        print(f"[PLANNING AGENT ERROR] {e}")
        return {
            "success": False,
            "message": "Impossible de créer le rendez-vous.",
            "request_id": payload.request_id
        }


@app.post("/webhook/orchestrator")
async def webhook_orchestrator(payload: OrchestratorPayload):
    return await orchestrator_endpoint(payload)


@app.post("/webhook/mail")
async def webhook_mail(payload: AgentPayload):
    return await mail_agent_endpoint(payload)


@app.post("/webhook/planning")
async def webhook_planning(payload: AgentPayload):
    return await planning_agent_endpoint(payload)


@app.post("/agent/relance")
async def relance_agent_endpoint(payload: AgentPayload):
    try:
        return await run_relance_agent(payload.model_dump())
    except Exception as e:
        print(f"[RELANCE AGENT ERROR] {e}")
        return {
            "success": False,
            "message": "Impossible de créer le rappel.",
            "request_id": payload.request_id
        }



@app.post("/agent/taches")
async def taches_agent_endpoint(payload: AgentPayload):
    try:
        return await run_taches_agent(payload.model_dump())
    except Exception as e:
        print(f"[TACHES AGENT ERROR] {e}")
        return {
            "success": False,
            "message": "Impossible de noter la tâche.",
            "request_id": payload.request_id
        }


@app.post("/agent/assistant")
async def assistant_agent_endpoint(payload: AgentPayload):
    try:
        return await run_assistant_agent(payload.model_dump())
    except Exception as e:
        print(f"[ASSISTANT AGENT ERROR] {e}")
        return {
            "success": False,
            "message": f"Erreur agent assistant: {str(e)}",
            "request_id": payload.request_id
        }


@app.post("/agent/redaction")
async def redaction_agent_endpoint(payload: AgentPayload):
    try:
        return await run_redaction_agent(payload.model_dump())
    except Exception as e:
        print(f"[REDACTION AGENT ERROR] {e}")
        return {
            "success": False,
            "message": f"Erreur agent redaction: {str(e)}",
            "request_id": payload.request_id
        }

@app.post("/webhook/relance")
async def webhook_relance(payload: AgentPayload):
    return await relance_agent_endpoint(payload)



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)


class StyleCreatePayload(BaseModel):
    user_id: str
    label: str
    key: Optional[str] = None
    id: Optional[str] = None
    style_id: Optional[str] = None
    example_message: Optional[str] = ""
    opening: Optional[str] = ""
    closing: Optional[str] = ""


@app.get("/styles/{user_id}")
async def styles_list(user_id: str):
    try:
        styles = list_styles(user_id)
        return {"success": True, "styles": styles}
    except Exception as e:
        return {"success": False, "message": str(e), "styles": []}


@app.post("/styles")
async def styles_create(payload: StyleCreatePayload):
    try:
        return create_style(payload.user_id, payload.model_dump())
    except Exception as e:
        return {"success": False, "message": str(e)}



class TaskPayload(BaseModel):
    user_id: Optional[str] = None
    titre: Optional[str] = None
    due_date: Optional[str] = None
    status: Optional[str] = None
    task_id: Optional[str] = None


@app.get("/tasks")
async def tasks_list(user_id: str = ""):
    return list_tasks(user_id)


@app.post("/tasks")
async def tasks_create(payload: TaskPayload):
    return create_task(payload.user_id or "", payload.titre or "", payload.due_date)


@app.patch("/tasks/{task_id}")
async def tasks_update(task_id: str, payload: TaskPayload):
    return update_task(payload.user_id or "", task_id, payload.status, payload.titre)


@app.delete("/tasks/{task_id}")
async def tasks_delete(task_id: str, user_id: str = ""):
    return delete_task(user_id, task_id)


class ContactPatchPayload(BaseModel):
    user_id: Optional[str] = None
    tags: Optional[list] = None
    notes_internes: Optional[str] = None


@app.get("/contacts/{contact_id}/activity")
async def contact_activity(contact_id: str, user_id: str = ""):
    return get_activity(user_id, contact_id)


@app.patch("/contacts/{contact_id}")
async def contact_patch(contact_id: str, payload: ContactPatchPayload):
    return patch_contact(payload.user_id or "", contact_id, payload.tags, payload.notes_internes)



class SentMailPayload(BaseModel):
    user_id: Optional[str] = None
    contact_id: Optional[str] = None
    to_email: Optional[str] = None
    subject: Optional[str] = None


@app.post("/mails/sent")
async def mails_sent(payload: SentMailPayload):
    """Appelé après un envoi réel (dashboard ou Make)."""
    return log_sent_mail(
        payload.user_id or "",
        payload.subject or "",
        payload.to_email or "",
        payload.contact_id or "",
    )
