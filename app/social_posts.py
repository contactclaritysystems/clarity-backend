import os
import json
from fastapi import APIRouter, UploadFile, File, Form
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
    except Exception as e:
        print(f"[SOCIAL] supabase: {e}")
        supabase = None

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

class GeneratePayload(BaseModel):
    user_id: str
    theme: Optional[str] = ""
    platforms: List[str] = ["instagram"]
    with_emojis: Optional[bool] = True
    logo_url: Optional[str] = None

@router.post("/social/generate")
async def generate_posts(payload: GeneratePayload):
    if not OPENAI_API_KEY:
        return {"success": False, "message": "Clé OpenAI manquante."}

    user_id = payload.user_id
    theme = (payload.theme or "").strip()
    platforms = [p.lower() for p in (payload.platforms or ["instagram"])]
    with_emojis = payload.with_emojis if payload.with_emojis is not None else True

    print(f"[SOCIAL] with_emojis={with_emojis} platforms={platforms}")

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

    shared_image_url = None
    async with httpx.AsyncClient() as client:
        try:
            img_prompt = f"Photo professionnelle épurée, style moderne, sujet : {theme or context}. Pas de texte dans l'image. Lumière naturelle."
            img = await client.post(
                "https://api.openai.com/v1/images/generations",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={"model": "dall-e-2", "prompt": img_prompt, "n": 1, "size": "1024x1024"},
                timeout=60
            )
            print(f"[SOCIAL] image status: {img.status_code}")
            if img.status_code == 200:
                shared_image_url = img.json()["data"][0]["url"]
                print("[SOCIAL] image OK")
            else:
                print(f"[SOCIAL] image error: {img.text[:200]}")
        except Exception as e:
            print(f"[SOCIAL] image exception: {e}")

        posts = []
        for platform in platforms:
            if platform == "instagram":
                hashtag_rule = "3 à 5 hashtags à la fin."
            elif platform == "linkedin":
                hashtag_rule = "2 à 4 hashtags à la fin."
            else:
                hashtag_rule = "Aucun hashtag."

            if with_emojis:
                emoji_rule = "Utilise 1 à 3 smileys maximum."
            else:
                emoji_rule = "INTERDICTION STRICTE : aucun smiley, aucun emoji, aucun symbole emoji. Texte 100% sans emoji."

            prompt = f"""Tu es Clarity. Post pour {platform}.

Contexte : {context}
Thème : {theme or "aucun"}
Longueur : {length}

Règles obligatoires :
- N’invente rien.
- {emoji_rule}
- {hashtag_rule}

Réponds uniquement en JSON :
{{"caption": "texte sans emoji si interdit", "hashtags": ["#a"]}}"""

            try:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.4
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
                posts.append({
                    "platform": platform,
                    "caption": data.get("caption", ""),
                    "image_url": shared_image_url,
                    "hashtags": data.get("hashtags", [])
                })
            except Exception as e:
                print(f"[SOCIAL] text {platform}: {e}")
                posts.append({
                    "platform": platform,
                    "caption": "Impossible de générer.",
                    "image_url": shared_image_url,
                    "hashtags": []
                })

    return {"success": True, "posts": posts, "shared_image_url": shared_image_url}

@router.post("/social/logo")
async def upload_logo(user_id: str = Form(...), file: UploadFile = File(...)):
    if not supabase:
        return {"success": False, "message": "Supabase non configuré."}
    try:
        content = await file.read()
        path = f"{user_id}/logo.png"
        supabase.storage.from_("social-logos").upload(
            path, content, {"content-type": file.content_type or "image/png", "upsert": "true"}
        )
        public_url = supabase.storage.from_("social-logos").get_public_url(path)
        supabase.table("profiles").update({"social_logo_url": public_url}).eq("id", user_id).execute()
        return {"success": True, "logo_url": public_url}
    except Exception as e:
        print(f"[SOCIAL] logo: {e}")
        return {"success": False, "message": "Erreur logo."}
