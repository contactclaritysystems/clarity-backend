import os
import json
import random
import base64
import asyncio
import uuid
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

# Stockage des jobs en mémoire
JOBS = {}

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
    carousel: Optional[bool] = False

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

async def generate_single_image(client: httpx.AsyncClient, prompt: str, logo_url: str = None) -> str:
    try:
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
        print(f"[SOCIAL] generate_single_image: {e}")
        return None

async def generate_text_for_platform(client: httpx.AsyncClient, platform: str, context: str, theme: str, with_emojis: bool, feedback_text: str, website: str) -> dict:
    if platform == "instagram":
        rules = """
- Accroche ≤125 caractères, parle du problème du lecteur.
- Texte : 80 à 150 mots.
- 3 à 5 hashtags précis à la fin.
- Jamais d'URL. Terminer par « Lien en bio ».
- 1er commentaire : complément utile + question différente, SANS lien."""
    elif platform == "facebook":
        rules = """
- Accroche ≤125 caractères.
- Texte : 60 à 120 mots, ton conversation.
- 0 à 2 hashtags.
- Lien uniquement dans un 2e commentaire (pas dans le post ni le 1er).
- 1er commentaire : complément + question, SANS lien."""
    elif platform == "linkedin":
        rules = """
- 2 premières lignes d'accroche fortes.
- Texte : 150 à 250 mots, aéré, professionnel.
- 0 à 3 hashtags.
- Pas de lien dans le post ni le 1er commentaire (envoi en privé de préférence).
- 1er commentaire : complément + question, SANS lien."""
    else:
        rules = "- Texte naturel. Pas de lien dans le post."

    emoji_rule = "0 à 3 emojis maximum, seulement utiles." if with_emojis else "Aucun emoji."
    feedback_block = f"\n\nRetours précédents (à respecter) :\n{feedback_text}" if feedback_text else ""
    website_info = f"\nSite : {website}" if website else ""

    prompt = f"""Tu es l'assistant de rédaction de Clarity. Tu crées des publications image + texte pour artisans, TPE et indépendants français.

Objectif : vraies interactions (commentaires, partages, enregistrements), pas des likes vides.

Règles des algorithmes à appliquer :
- Accroche dans les 125 premiers caractères.
- Pas de lien externe dans le texte.
- Pas d'appels à l'engagement forcés.
- Texte naturel, pas générique.
- Utilise de vrais sauts de ligne entre les paragraphes.

{rules}
- {emoji_rule}
- 2-3 mots en gras Unicode dans la légende.
- Ne jamais inventer de résultats ou de fonctionnalités.
- Structure : problème → ce qui change → preuve.
- Varie les angles, les personnages et les idées par rapport aux générations précédentes.
{website_info}
{feedback_block}

Contexte activité : {context}
Thème : {theme or "aucun"}

Réponds uniquement en JSON :
{{"caption": "texte avec vrais sauts de ligne \\n entre les paragraphes", "first_comment": "complément + question sans lien (toujours remplir)", "image_brief": "description précise"}}"""

    try:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4,
                "max_tokens": 600
            },
            timeout=25
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        return {
            "platform": platform,
            "caption": data.get("caption", "").replace("\\n", "\n"),
            "first_comment": data.get("first_comment") or "Petit bonus : pense à répondre à tous les commentaires dans la première heure. Et toi, quelle tâche te prend le plus de temps le soir ?",
            "image_brief": data.get("image_brief", "")
        }
    except Exception as e:
        print(f"[SOCIAL] text {platform}: {e}")
        return {
            "platform": platform,
            "caption": "Impossible de générer.",
            "first_comment": "",
            "image_brief": ""
        }

async def run_generation(job_id: str, payload: GeneratePayload):
    """Génération en arrière-plan avec mise à jour du statut."""
    JOBS[job_id] = {"status": "writing_text", "progress": 10, "result": None, "error": None}
    
    try:
        user_id = payload.user_id
        theme = (payload.theme or "").strip()
        if theme == "Autre (précisez)":
            theme = (payload.theme_other or "").strip()
        platforms = [p.lower() for p in (payload.platforms or ["instagram"])]
        with_emojis = payload.with_emojis if payload.with_emojis is not None else True
        use_carousel = payload.carousel or False

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

        async with httpx.AsyncClient() as client:
            # Textes
            JOBS[job_id]["status"] = "writing_text"
            JOBS[job_id]["progress"] = 20
            
            text_tasks = [
                generate_text_for_platform(client, p, context, theme, with_emojis, feedback_text, website)
                for p in platforms
            ]
            text_results = await asyncio.gather(*text_tasks, return_exceptions=True)

            # Image
            JOBS[job_id]["status"] = "generating_image"
            JOBS[job_id]["progress"] = 50
            
            style = random.choice(STYLES)
            base_prompt = f"{style}. Activité : {context}. Thème : {theme or 'professionnel'}. Lumière contrastée, composition dynamique. Pas de texte dans l'image. Espace libre en haut à droite pour un logo. Format vertical 4:5."

            if use_carousel:
                prompts = [
                    base_prompt + " Vue principale du sujet, cadrage large.",
                    base_prompt + " Détail ou angle différent, focus sur une action ou un résultat.",
                    base_prompt + " Scène complémentaire, avant/après ou contexte d'utilisation."
                ]
                img_tasks = [generate_single_image(client, p, logo_url) for p in prompts]
            else:
                img_tasks = [generate_single_image(client, base_prompt, logo_url)]

            image_results = await asyncio.gather(*img_tasks, return_exceptions=True)
            image_urls = [r for r in image_results if isinstance(r, str)]

            # Résultat final
            posts = []
            for text in text_results:
                if isinstance(text, dict):
                    text["image_url"] = image_urls[0] if image_urls else None
                    text["image_urls"] = image_urls
                    posts.append(text)
                else:
                    posts.append({
                        "platform": "unknown",
                        "caption": "Erreur de génération.",
                        "first_comment": "",
                        "image_brief": "",
                        "image_url": image_urls[0] if image_urls else None,
                        "image_urls": image_urls
                    })

            JOBS[job_id] = {
                "status": "done",
                "progress": 100,
                "result": {
                    "success": True,
                    "posts": posts,
                    "image_urls": image_urls,
                    "carousel": use_carousel
                },
                "error": None
            }

    except Exception as e:
        print(f"[SOCIAL] job {job_id} error: {e}")
        JOBS[job_id] = {"status": "error", "progress": 0, "result": None, "error": str(e)}

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
            return {
                "success": True,
                "advice": res.data.get("content") or "",
                "updated_at": res.data.get("updated_at")
            }
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
