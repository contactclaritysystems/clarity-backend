import os
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from supabase import create_client
import httpx

router = APIRouter()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

class GeneratePayload(BaseModel):
    user_id: str
    theme: Optional[str] = ""
    platforms: List[str] = ["instagram"]

@router.post("/social/generate")
async def generate_posts(payload: GeneratePayload):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase non configuré")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OpenAI non configuré")

    user_id = payload.user_id
    theme = (payload.theme or "").strip()
    platforms = payload.platforms or ["instagram"]

    # Récupérer le contexte
    profile = supabase.table("profiles").select(
        "social_context, social_length, email"
    ).eq("id", user_id).single().execute()

    context = (profile.data or {}).get("social_context") or "activité non précisée"
    length = (profile.data or {}).get("social_length") or "moyen"

    posts = []

    async with httpx.AsyncClient() as client:
        for platform in platforms:
            platform = platform.lower()
            prompt = f"""Tu es Clarity. Tu rédiges une publication pour {platform}.

Contexte de l'activité :
{context}

Thème demandé : {theme or "aucun thème particulier"}

Longueur : {length}

Règles :
- Accroche forte dès la première ligne.
- Une seule idée claire.
- Texte naturel et fluide.
- Adapte le ton et la longueur à {platform}.
- Ajoute 3 à 5 hashtags pertinents.
- N’invente jamais de résultats ou de chiffres.

Réponds uniquement en JSON valide :
{{"caption": "texte de la publication", "hashtags": ["#hashtag1", "#hashtag2"]}}"""

            try:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.7
                    },
                    timeout=60
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                content = content.strip()
                if content.startswith("```"):
                    content = content.split("```")[1]
                    if content.startswith("json"):
                        content = content[4:]
                data = json.loads(content.strip())

                caption = data.get("caption", "")
                hashtags = data.get("hashtags", [])

                # Enregistrer
                supabase.table("social_posts").insert({
                    "user_id": user_id,
                    "angle": theme or "publication",
                    "body": caption,
                    "platform": platform,
                    "status": "generated"
                }).execute()

                posts.append({
                    "platform": platform,
                    "caption": caption,
                    "image_url": None,  # image plus tard
                    "hashtags": hashtags
                })
            except Exception as e:
                print(f"[SOCIAL] Erreur {platform}: {e}")
                posts.append({
                    "platform": platform,
                    "caption": "Impossible de générer ce post pour le moment.",
                    "image_url": None,
                    "hashtags": []
                })

    return {"success": True, "posts": posts}
