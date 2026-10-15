import os
import json
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List
import httpx

router = APIRouter()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

class GeneratePayload(BaseModel):
    user_id: str
    theme: Optional[str] = ""
    platforms: List[str] = ["instagram"]

@router.post("/social/generate")
async def generate_posts(payload: GeneratePayload):
    if not OPENAI_API_KEY:
        return {"success": False, "message": "Clé OpenAI manquante sur le serveur."}

    theme = (payload.theme or "").strip()
    platforms = payload.platforms or ["instagram"]
    posts = []

    async with httpx.AsyncClient() as client:
        for platform in platforms:
            platform = platform.lower()
            prompt = f"""Tu es Clarity. Tu rédiges une publication pour {platform}.

Thème : {theme or "aucun thème particulier"}

Règles :
- Accroche forte dès la première ligne.
- Une seule idée claire.
- Texte naturel.
- Adapte le ton à {platform}.
- 3 à 5 hashtags.
- N’invente rien.

Réponds uniquement en JSON :
{{"caption": "texte", "hashtags": ["#a", "#b"]}}"""

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
