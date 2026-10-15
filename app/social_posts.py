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
    with_emojis: Optional[bool] = True

@router.post("/social/generate")
async def generate_posts(payload: GeneratePayload):
    if not OPENAI_API_KEY:
        return {"success": False, "message": "Clé OpenAI manquante."}

    user_id = payload.user_id
    theme = (payload.theme or "").strip()
    platforms = [p.lower() for p in (payload.platforms or ["instagram"])]
    with_emojis = bool(payload.with_emojis)

    context = "activité non précisée"
    length = "moyen"
    if supabase:
        try:
            res = supabase.table("profiles").select("social_context, social_length").eq("id", user_id).single().execute()
            if res.data:
                context = res.data.get("social_context") or context
                length = res.data.get("social_length") or length
        except Exception as e:
            print(f"[SOCIAL] profile: {e}")

    posts = []
    async with httpx.AsyncClient() as client:
        for platform in platforms:
            emoji_rule = "Utilise 1 à 3 smileys maximum." if with_emojis else "Aucun smiley ni emoji."

            prompt = f"""Tu es Clarity. Publication professionnelle pour {platform}.

Contexte :
{context}

Thème : {theme or "aucun"}

Longueur : {length}

Règles :
- Uniquement ce qui est dans le contexte. N’invente rien.
- Accroche concrète dès la 1ère ligne.
- Une seule idée claire.
- Texte naturel.
- Ton adapté à {platform}.
- {emoji_rule}
- 3 à 5 hashtags à la fin.

Réponds uniquement en JSON :
{{"caption": "texte", "hashtags": ["#a", "#b"]}}"""

            try:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.6
                    },
                    timeout=40
                )
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"].strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                data = json.loads(raw.strip())
                caption = data.get("caption", "")
                hashtags = data.get("hashtags", [])

                # Image
                image_url = None
                try:
                    img_prompt = f"Photo professionnelle épurée, style {platform}, sujet : {theme or context}. Pas de texte. Lumière naturelle."
                    img = await client.post(
                        "https://api.openai.com/v1/images/generations",
                        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                        json={
                            "model": "dall-e-3",
                            "prompt": img_prompt,
                            "n": 1,
                            "size": "1024x1024"
                        },
                        timeout=50
                    )
                    img.raise_for_status()
                    image_url = img.json()["data"][0]["url"]
                except Exception as e:
                    print(f"[SOCIAL] image {platform}: {e}")

                posts.append({
                    "platform": platform,
                    "caption": caption,
                    "image_url": image_url,
                    "hashtags": hashtags
                })
            except Exception as e:
                print(f"[SOCIAL] {platform}: {e}")
                posts.append({
                    "platform": platform,
                    "caption": "Impossible de générer.",
                    "image_url": None,
                    "hashtags": []
                })

    return {"success": True, "posts": posts}
