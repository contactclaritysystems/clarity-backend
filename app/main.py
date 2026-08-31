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
from app.writing_styles import list_styles, create_style
from app.capabilities import public_payload
from app.feature_requests import save_feature_request
from app.notifications import run_notification_pass, send_email

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
