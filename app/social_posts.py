import os
import json
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List
from supabase import create_client
import httpx

router = APIRouter()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        supabase = None

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

class GeneratePayload(BaseModel):
    user_id: str
    theme: Optional[str] = ""
    platforms: List[str] = ["instagram"]

@router.post("/social/generate")
async def generate_posts(payload: GeneratePayload):
    if not OPENAI_API_KEY:
        return {"success": False, "message": "Clé OpenAI manquante."}

    user_id = payload.user_id
    theme = (payload.theme or "").strip()
    platforms = payload.platforms or ["instagram"]

    # Récupérer le contexte réel
    context = "activité non précisée"
    length = "moyen"
    if supabase:
        try:
            res = supabase.table("profiles").select("social_context, social_length").eq("id", user_id).single().execute()
            if res.data:
                context = res.data.get("social_context") or context
                length = res.data.get("social_length") or length
        except Exception as e:
            print(f"[SOCIAL] profile fetch: {e}")

    posts = []
    async with httpx.AsyncClient() as client:
        for platform in platforms:
            platform = platform.lower()
            prompt = f"""Tu es Clarity. Tu rédiges une publication pour {platform}.

Contexte de l'activité de l'utilisateur :
{context}

Thème demandé : {theme or "aucun"}

Longueur souhaitée : {length}

Règles :
- Parle uniquement de ce qui est dans le contexte.
- Accroche forte dès la première ligne.
- Une seule idée claire liée à l'activité.
- Texte naturel et fluide.
- Adapte le ton à {platform}.
- 3 à 5 hashtags pertinents.
- N’invente jamais de résultats, chiffres ou témoignages.

Réponds uniquement en JSON valide :
{{"caption": "texte de la publication", "hashtags": ["#a", "#b"]}}"""

            try:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.7
                    },
                    timeout=45
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"].strip()
                if content.startswith("```"):
                    content = content.split("```")[1]
                    if content.startswith("json"):
                        content = content[4:]
                data = json.loads(content.strip())
                posts.append({
                    "platform": platform,
                    "caption": data.get("caption", ""),
                    "image_url": None,
                    "hashtags": data.get("hashtags", [])
                })
            except Exception as e:
                print(f"[SOCIAL] {platform}: {e}")
                posts.append({
                    "platform": platform,
                    "caption": "Impossible de générer ce post pour le moment.",
                    "image_url": None,
                    "hashtags": []
                })

    return {"success": True, "posts": posts}
