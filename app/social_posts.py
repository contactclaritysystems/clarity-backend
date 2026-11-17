import os
import json
import random
import base64
import asyncio
import uuid
import re
from io import BytesIO
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks
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

JOBS = {}

SYSTEM_PROMPT = """Tu es l'assistant de rédaction de Clarity. Tu crées des publications « image + texte » pour Instagram, Facebook et LinkedIn, pour des artisans, des TPE et des indépendants français.
Ton objectif : un maximum de vraies interactions (commentaires, partages, enregistrements), pas des likes vides.

INTERDICTIONS ABSOLUES :
1. Ne jamais inventer de clients, témoignages, avis, chiffres ou résultats. Décris une situation concrète à la place.
2. Texte brut uniquement. Aucun markdown : pas de **, #, _ ni >. Pour mettre en valeur, utilise un retour à la ligne ou un emoji.
3. Mots interdits : Imaginez, Saviez-vous, Vous êtes débordé, ce qui compte vraiment, gagnez un temps précieux, dites adieu, ne laissez plus, libérez-vous, n'est pas juste une application, au quotidien, en toute sérénité, révolutionner, booster, Bonjour à tous.
4. Les 3 versions ne doivent jamais partager la même phrase.
5. Format de sortie obligatoire : brief image, texte, 1er commentaire pour chaque réseau.

TON :
- Instagram et Facebook : tutoiement.
- LinkedIn : vouvoiement ou « je ».
- Concret, mots simples, détails de terrain.

ACCROCHE (1re ligne) :
- 125 caractères max, parle du problème du lecteur, jamais du produit.
- Types : situation vécue, chiffre précis, contre-intuitif, question ciblée, avant/après, erreur fréquente.
- Interdit en ouverture : Bonjour à tous, Aujourd'hui je voulais, Saviez-vous que, emoji seul.

CORPS :
- Phrases courtes, une idée par ligne, lignes vides entre blocs.
- Structure : problème → ce qui change → preuve.
- 0 à 3 emojis max.
- Fin : UNE seule action (question, partage, enregistrement, essai via mot-clé).

1ER COMMENTAIRE :
- Complément utile absent du post + question différente.
- 1 à 3 phrases, même ton.
- Jamais de lien.

FORMAT DE SORTIE (JSON uniquement) :
{
  "instagram": {
    "brief": "description précise de l'image",
    "caption": "texte avec sauts de ligne",
    "first_comment": "complément + question"
  },
  "facebook": {
    "brief": "...",
    "caption": "...",
    "first_comment": "..."
  },
  "linkedin": {
    "brief": "...",
    "caption": "...",
    "first_comment": "..."
  }
}

Réponds uniquement avec le JSON, rien d'autre."""

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'\*\*|__', '', text)
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
    return text.strip()

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

async def generate_image(client: httpx.AsyncClient, brief: str, logo_url: str = None) -> str:
    try:
        prompt = f"Photo réaliste, style smartphone, pas de banque d'images, pas de personne en costume au bureau. {brief}. Format vertical 4:5. Pas de texte dans l'image sauf si explicitement demandé."
        img = await client.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={"model": "gpt-image-2.5-sunburst", "prompt": prompt, "n": 1, "size": "1024x1024"},
            timeout=60
        )
        if img.status_code != 200:
            print(f"[SOCIAL] image error: {img.text[:200]}")
            return None

        data = img.json()
        item = data["data"][0]
        if "b64_json" in item:
            img_bytes = base64.b64decode(item["b64_json"])
        elif "url" in item:
            r = await client.get(item["url"], timeout=30)
            img_bytes = r.content
        else:
            return None

        if logo_url:
            try:
                logo_resp = await client.get(logo_url, timeout=15)
                if logo_resp.status_code == 200:
                    base = Image.open(BytesIO(img_bytes)).convert("RGBA")
                    final = overlay_logo(base, logo_resp.content)
                    buf = BytesIO()
                    final.convert("RGB").save(buf, format="JPEG", quality=85)
                    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
            except Exception as e:
                print(f"[SOCIAL] logo: {e}")

        return "data:image/png;base64," + base64.b64encode(img_bytes).decode()
    except Exception as e:
        print(f"[SOCIAL] generate_image: {e}")
        return None

async def generate_posts(client: httpx.AsyncClient, context: str, theme: str, with_emojis: bool, feedback_text: str, website: str) -> dict:
    emoji_rule = "0 à 3 emojis maximum." if with_emojis else "Aucun emoji."
    feedback_block = f"\n\nRetours précédents à respecter :\n{feedback_text}" if feedback_text else ""
    website_info = f"\nSite : {website}" if website else ""

    user_msg = f"""Contexte activité : {context}
Thème : {theme or "aucun"}
{emoji_rule}
{website_info}
{feedback_block}

Génère les 3 versions (Instagram, Facebook, LinkedIn)."""

    try:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg}
                ],
                "temperature": 0.75,
                "max_tokens": 2000
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
        
        # Nettoyage markdown
        for platform in data:
            if isinstance(data[platform], dict):
                data[platform]["caption"] = clean_text(data[platform].get("caption", ""))
                data[platform]["first_comment"] = clean_text(data[platform].get("first_comment", ""))
        
        return data
    except Exception as e:
        print(f"[SOCIAL] generate_posts: {e}")
        return {}

async def progress_ticker(job_id: str, start: int, end: int, duration: float):
    steps = end - start
    if steps <= 0 or duration <= 0:
        return
    delay = duration / steps
    for i in range(steps):
        if JOBS.get(job_id, {}).get("status") == "done":
            return
        JOBS[job_id]["progress"] = start + i + 1
        await asyncio.sleep(delay)

async def run_generation(job_id: str, payload):
    JOBS[job_id] = {"status": "starting", "progress": 0, "result": None, "error": None}
    
    try:
        user_id = payload.user_id
        theme = (payload.theme or "").strip()
        if theme == "Autre (précisez)":
            theme = (payload.theme_other or "").strip()
        platforms = [p.lower() for p in (payload.platforms or ["instagram"])]
        with_emojis = payload.with_emojis if payload.with_emojis is not None else True

        context = "activité non précisée"
        logo_url = payload.logo_url
        website = ""
        if supabase:
            try:
                res = supabase.table("profiles").select("social_context, social_logo_url, website").eq("id", user_id).single().execute()
                if res.data:
                    context = res.data.get("social_context") or context
                    logo_url = logo_url or res.data.get("social_logo_url")
                    website = (res.data.get("website") or "").strip()
            except Exception as e:
                print(f"[SOCIAL] profile: {e}")

        feedback_text = get_recent_feedback(user_id)

        async with httpx.AsyncClient() as client:
            # Phase 1 : rédaction
            JOBS[job_id]["status"] = "writing_text"
            ticker1 = asyncio.create_task(progress_ticker(job_id, 0, 30, 8))
            
            posts_data = await generate_posts(client, context, theme, with_emojis, feedback_text, website)
            ticker1.cancel()

            # Phase 2 : images
            JOBS[job_id]["status"] = "generating_image"
            JOBS[job_id]["progress"] = 30
            ticker2 = asyncio.create_task(progress_ticker(job_id, 30, 95, 20))
            
            posts = []
            for platform in platforms:
                p_data = posts_data.get(platform, {})
                brief = p_data.get("brief", "artisan au travail, style réaliste")
                image_url = await generate_image(client, brief, logo_url)
                
                posts.append({
                    "platform": platform,
                    "caption": p_data.get("caption", ""),
                    "first_comment": p_data.get("first_comment", ""),
                    "image_brief": brief,
                    "image_url": image_url,
                    "image_urls": [image_url] if image_url else []
                })
            
            ticker2.cancel()

            JOBS[job_id] = {
                "status": "done",
                "progress": 100,
                "result": {
                    "success": True,
                    "posts": posts,
                    "image_urls": [p["image_url"] for p in posts if p.get("image_url")],
                    "carousel": False
                },
                "error": None
            }

    except Exception as e:
        print(f"[SOCIAL] job {job_id} error: {e}")
        JOBS[job_id] = {"status": "error", "progress": 0, "result": None, "error": str(e)}

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

@router.post("/social/generate")
async def start_generate(payload: GeneratePayload, background_tasks: BackgroundTasks):
    if not OPENAI_API_KEY:
        return {"success": False, "message": "Clé OpenAI manquante."}
    
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "starting", "progress": 0, "result": None, "error": None}
    background_tasks.add_task(run_generation, job_id, payload)
    return {"success": True, "job_id": job_id}

@router.get("/social/generate/status")
async def get_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return {"success": False, "message": "Job introuvable"}
    return {
        "success": True,
        "status": job["status"],
        "progress": job["progress"],
        "result": job["result"],
        "error": job["error"]
    }

@router.get("/social/advice")
async def get_social_advice():
    if not supabase:
        return {"success": False, "advice": "Conseils non disponibles.", "updated_at": None}
    try:
        res = supabase.table("social_advice").select("content, updated_at").eq("id", 1).single().execute()
        if res.data:
            return {"success": True, "advice": res.data.get("content") or "", "updated_at": res.data.get("updated_at")}
    except Exception as e:
        print(f"[SOCIAL] advice: {e}")
    return {"success": False, "advice": "Conseils non disponibles.", "updated_at": None}

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
