import os
import json
import random
import base64
from io import BytesIO
from fastapi import APIRouter, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional, List
from supabase import create_client
import httpx
from PIL import Image

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

STYLES = [
    "photo professionnelle moderne, lumière naturelle, fond épuré",
    "style photo lifestyle, ambiance chaleureuse, lumière douce",
    "photo corporate élégante, fond clair, composition aérée",
    "style minimaliste, couleurs neutres, lumière du jour",
    "photo dynamique, angle légèrement bas, ambiance positive",
    "style contemporain, fond doux, mise en scène simple",
]

THEMES = [
    "Faire tester le produit / service",
    "Gagner des abonnés",
    "Avoir plus de commentaires",
    "Obtenir des messages privés",
    "Annoncer une nouveauté",
    "Montrer un résultat client",
    "Conseil / astuce utile",
    "Coulisses / quotidien",
    "Offre limitée / promo",
    "Autre (précisez)"
]

class GeneratePayload(BaseModel):
    user_id: str
    theme: Optional[str] = ""
    theme_other: Optional[str] = ""
    platforms: List[str] = ["instagram"]
    with_emojis: Optional[bool] = True
    logo_url: Optional[str] = None

class FeedbackPayload(BaseModel):
    user_id: str
    theme: Optional[str] = ""
    liked: bool
    comment: Optional[str] = ""

def overlay_logo(base_img: Image.Image, logo_bytes: bytes) -> Image.Image:
    logo = Image.open(BytesIO(logo_bytes)).convert("RGBA")
    
    max_w = int(base_img.width * 0.26)
    ratio = max_w / logo.width
    logo = logo.resize((max_w, int(logo.height * ratio)), Image.LANCZOS)
    
    margin = int(base_img.width * 0.03)
    pos = (base_img.width - logo.width - margin, margin)
    
    base_img = base_img.convert("RGBA")
    base_img.paste(logo, pos, logo)
    return base_img

def get_recent_feedback(user_id: str) -> str:
    if not supabase:
        return ""
    try:
        res = supabase.table("social_feedback").select("liked, comment").eq("user_id", user_id).order("created_at", desc=True).limit(5).execute()
        if not res.data:
            return ""
        lines = []
        for f in res.data:
            status = "aime" if f.get("liked") else "n'aime pas"
            comment = (f.get("comment") or "").strip()
            if comment:
                lines.append(f"- {status} : {comment}")
            else:
                lines.append(f"- {status}")
        return "\n".join(lines)
    except Exception as e:
        print(f"[SOCIAL] feedback read: {e}")
        return ""

@router.post("/social/feedback")
async def save_feedback(payload: FeedbackPayload):
    if not supabase:
        return {"success": False, "message": "Supabase non configuré."}
    try:
        supabase.table("social_feedback").insert({
            "user_id": payload.user_id,
            "theme": payload.theme or "",
            "liked": payload.liked,
            "comment": payload.comment or ""
        }).execute()
        return {"success": True}
    except Exception as e:
        print(f"[SOCIAL] feedback save: {e}")
        return {"success": False, "message": "Erreur enregistrement."}

@router.post("/social/generate")
async def generate_posts(payload: GeneratePayload):
    if not OPENAI_API_KEY:
        return {"success": False, "message": "Clé OpenAI manquante."}

    user_id = payload.user_id
    theme = (payload.theme or "").strip()
    if theme == "Autre (précisez)":
        theme = (payload.theme_other or "").strip()
    platforms = [p.lower() for p in (payload.platforms or ["instagram"])]
    with_emojis = payload.with_emojis if payload.with_emojis is not None else True

    print(f"[SOCIAL] with_emojis={with_emojis} platforms={platforms}")

    context = "activité non précisée"
    length = "moyen"
    logo_url = payload.logo_url
    website = ""
    if supabase:
        try:
            res = supabase.table("profiles").select("social_context, social_length, social_logo_url, website").eq("id", user_id).single().execute()
            if res.data:
                context = res.data.get("social_context") or context
                length = res.data.get("social_length") or length
                logo_url = logo_url or res.data.get("social_logo_url")
                website = (res.data.get("website") or "").strip()
        except Exception as e:
            print(f"[SOCIAL] profile: {e}")

    feedback_text = get_recent_feedback(user_id)

    shared_image_url = None
    async with httpx.AsyncClient() as client:
        try:
            style = random.choice(STYLES)
            img_prompt = f"{style}. Activité : {context}. Thème : {theme or 'professionnel'}. Lumière contrastée, composition dynamique, regard fort. Pas de texte dans l'image. Espace libre en haut à droite pour un logo. Image cohérente avec l'activité décrite."
            img = await client.post(
                "https://api.openai.com/v1/images/generations",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={"model": "gpt-image-2.5-sunburst", "prompt": img_prompt, "n": 1, "size": "1024x1024"},
                timeout=90
            )
            print(f"[SOCIAL] image status: {img.status_code}")
            if img.status_code == 200:
                data = img.json()
                item = data["data"][0]
                if "b64_json" in item:
                    img_bytes = base64.b64decode(item["b64_json"])
                elif "url" in item:
                    r = await client.get(item["url"])
                    img_bytes = r.content
                else:
                    img_bytes = None

                if img_bytes and logo_url:
                    try:
                        logo_resp = await client.get(logo_url)
                        if logo_resp.status_code == 200:
                            base = Image.open(BytesIO(img_bytes)).convert("RGBA")
                            final = overlay_logo(base, logo_resp.content)
                            buf = BytesIO()
                            final.convert("RGB").save(buf, format="JPEG", quality=90)
                            shared_image_url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
                            print("[SOCIAL] logo applied")
                        else:
                            shared_image_url = "data:image/png;base64," + base64.b64encode(img_bytes).decode()
                    except Exception as e:
                        print(f"[SOCIAL] logo overlay: {e}")
                        shared_image_url = "data:image/png;base64," + base64.b64encode(img_bytes).decode()
                elif img_bytes:
                    shared_image_url = "data:image/png;base64," + base64.b64encode(img_bytes).decode()
                print("[SOCIAL] image OK")
            else:
                print(f"[SOCIAL] image error: {img.text[:300]}")
        except Exception as e:
            print(f"[SOCIAL] image exception: {e}")

        posts = []
        for platform in platforms:
            if platform == "instagram":
                hashtag_count = "8 à 12 hashtags"
            elif platform == "linkedin":
                hashtag_count = "3 à 5 hashtags"
            else:
                hashtag_count = "4 à 6 hashtags"

            if with_emojis:
                emoji_rule = "Utilise 1 à 3 smileys maximum dans la légende."
            else:
                emoji_rule = "INTERDICTION STRICTE : aucun smiley, aucun emoji dans la légende."

            feedback_block = ""
            if feedback_text:
                feedback_block = f"\n\nRetours précédents de cet utilisateur (à respecter) :\n{feedback_text}"

            website_line = f"\n- Ajoute le lien du site à la fin du premier commentaire : {website}" if website else ""

            prompt = f"""Tu es Clarity. Tu écris un post {platform} pour un indépendant / artisan.

Contexte de l'activité : {context}
Thème demandé : {theme or "aucun thème particulier"}
Longueur : {length}

Règles strictes :
- Écris comme un humain, pas comme une pub.
- Pas de formules toutes faites.
- Va droit au but, ton naturel et direct.
- INTERDICTION de répéter la même idée à la fin.
- {emoji_rule}
- Dans la légende (caption) : mets 2-3 mots importants en gras Unicode. Jamais de hashtags dans la légende.
- Dans first_comment : écris un vrai commentaire naturel (1 ou 2 phrases max) qui encourage à interagir ou à en savoir plus. Mets ensuite 4 à 6 hashtags maximum à la fin.{website_line}
- N’invente aucun détail qui n’est pas dans le contexte ou le thème.
{feedback_block}

Réponds uniquement en JSON :
{{"caption": "légende avec 2-3 mots en gras Unicode", "first_comment": "commentaire naturel + quelques hashtags"}}"""

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
                    "first_comment": data.get("first_comment", ""),
                    "image_url": shared_image_url
                })
            except Exception as e:
                print(f"[SOCIAL] text {platform}: {e}")
                posts.append({
                    "platform": platform,
                    "caption": "Impossible de générer.",
                    "first_comment": "",
                    "image_url": shared_image_url
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
