import os
import json
from fastapi import APIRouter, HTTPException
from supabase import create_client
import httpx

router = APIRouter()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

@router.post("/social/generate-test")
async def generate_test_post(user_id: str):
    # 1. Récupérer les réglages
    profile = supabase.table("profiles").select(
        "social_enabled, social_frequency, social_length, social_context, social_logo_url, email"
    ).eq("id", user_id).single().execute()

    if not profile.data or not profile.data.get("social_enabled"):
        raise HTTPException(status_code=400, detail="Publications automatiques non activées")

    context = profile.data.get("social_context") or "activité non précisée"
    length = profile.data.get("social_length") or "moyen"
    email = profile.data.get("email")

    # 2. Derniers angles
    recent = supabase.table("social_posts").select("angle").eq("user_id", user_id).order("created_at", desc=True).limit(12).execute()
    used_angles = [r["angle"] for r in (recent.data or []) if r.get("angle")]

    # 3. Génération texte
    prompt = f"""Tu es Clarity. Tu rédiges une publication réseaux sociaux.

Contexte :
{context}

Longueur : {length}

Objectif :
- Accroche forte dès la première ligne (donne envie de continuer).
- Une seule idée claire.
- Texte fluide, naturel, qui se lit jusqu’au bout.
- Fin qui pousse à réagir (commentaire, like ou message).
- N’invente jamais de résultats, chiffres, témoignages ou promesses non présents dans le contexte.
- Évite ces angles déjà utilisés : {', '.join(used_angles) or 'aucun'}.

Réponds uniquement en JSON valide :
{{"angle": "description courte du nouvel angle", "body": "texte complet de la publication"}}"""

    async with httpx.AsyncClient() as client:
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
        # Nettoyage basique si le modèle met des ```json
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        data = json.loads(content.strip())

    angle = data.get("angle", "publication")
    body = data.get("body", "")

    # 4. Enregistrer
    supabase.table("social_posts").insert({
        "user_id": user_id,
        "angle": angle,
        "body": body,
        "status": "sent"
    }).execute()

    return {
        "success": True,
        "angle": angle,
        "body": body,
        "email": email
    }
