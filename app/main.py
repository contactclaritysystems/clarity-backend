"""
Clarity Systems - Backend natif
"""

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from app.orchestrator import run_orchestrator
from app.agents.mail_agent import run_mail_agent
from app.agents.planning_agent import run_planning_agent
from app.agents.relance_agent import run_relance_agent

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
    instruction: str
    request_id: Optional[str] = None
    contact_id: Optional[str] = None


@app.get("/")
async def root():
    return {"status": "ok", "service": "Clarity Backend", "version": "0.2.0"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


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


@app.post("/webhook/relance")
async def webhook_relance(payload: AgentPayload):
    return await relance_agent_endpoint(payload)



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
