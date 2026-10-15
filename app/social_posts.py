import os
import json
import base64
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
    platforms = payload.platforms or ["instagram"]
    with_emojis = payload.with_emojis if payload.with_emojis is not None else True

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
            platform = platform.lower()

            emoji_rule = "Utilise 1 à 3 smileys pertinents, pas plus." if with_emojis else "N’utilise aucun smiley ni emoji."

            prompt = f"""Tu es Clarity. Tu rédiges une publication professionnelle pour {platform}.

Contexte de l'activité :
{context}

Thème : {theme or "aucun"}

Longueur : {length}

Règles strictes :
- Parle uniquement de ce qui est dans le contexte. N’invente rien.
- Accroche forte et concrète dès la première ligne.
- Une seule idée claire, utile pour le lecteur.
- Texte naturel, fluide, qui se lit jusqu’au bout.
- Adapte le ton et la longueur à {platform}.
- {emoji_rule}
- 3 à 5 hashtags pertinents à la fin.
- Pas de phrases bateau du type « libérez-vous » ou « concentrez-vous sur l’essentiel » si ce n’est pas dans le contexte.

Réponds uniquement en JSON valide :
{{"caption": "texte complet", "hashtags": ["#a", "#b"]}}"""

            try:
                # Texte
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.65
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
                caption = data.get("caption", "")
                hashtags = data.get("hashtags", [])

                # Image
                image_url = None
                try:
                    img_prompt = f"Photo professionnelle, style épuré, pour une publication {platform} sur : {theme or context}. Pas de texte dans l'image. Lumière naturelle, composition claire."
                    img_resp = await client.post(
                        "https://api.openai.com/v1/images/generations",
                        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                        json={
                            "model": "dall-e-3",
                            "prompt": img_prompt,
                            "n": 1,
                            "size": "1024x1024",
                            "quality": "standard"
                        },
                        timeout=60
                    )
                    img_resp.raise_for_status()
                    image_url = img_resp.json()["data"][0]["url"]
                except Exception as img_e:
                    print(f"[SOCIAL] image {platform}: {img_e}")

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
                    "caption": "Impossible de générer ce post pour le moment.",
                    "image_url": None,
                    "hashtags": []
                })

    return {"success": True, "posts": posts}
