import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter
from supabase import create_client
import httpx

router = APIRouter()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

PARIS = ZoneInfo("Europe/Paris")

DAY_MAP = {
    0: "lun",
    1: "mar",
    2: "mer",
    3: "jeu",
    4: "ven",
    5: "sam",
    6: "dim"
}

@router.post("/cron/social-posts")
async def send_scheduled_posts():
    if not supabase:
        return {"success": False, "message": "Supabase non configuré"}

    now = datetime.now(PARIS)
    current_day = DAY_MAP[now.weekday()]
    current_time = now.strftime("%H:%M")

    print(f"[SOCIAL CRON] {current_day} {current_time}")

    try:
        res = supabase.table("profiles").select(
            "id, email, social_context, social_length, social_logo_url, social_schedule_days, social_schedule_time"
        ).execute()

        users = res.data or []
        sent = 0

        for user in users:
            days = user.get("social_schedule_days") or []
            if isinstance(days, str):
                try:
                    days = json.loads(days)
                except:
                    days = []
            schedule_time = user.get("social_schedule_time") or "07:00"

            if current_day not in days:
                continue
            if schedule_time != current_time:
                continue

            async with httpx.AsyncClient() as client:
                gen = await client.post(
                    "http://localhost:8000/social/generate",
                    json={
                        "user_id": user["id"],
                        "theme": "Conseil / astuce utile",
                        "platforms": ["instagram"],
                        "with_emojis": True,
                        "logo_url": user.get("social_logo_url")
                    },
                    timeout=120
                )
                if gen.status_code != 200:
                    print(f"[SOCIAL CRON] gen fail {user['id']}")
                    continue
                data = gen.json()
                if not data.get("success") or not data.get("posts"):
                    continue

                post = data["posts"][0]
                caption = post.get("caption", "")
                image_url = post.get("image_url")

                print(f"[SOCIAL CRON] Post ready for {user.get('email')} : {caption[:80]}...")

                # TODO: brancher l'envoi mail ici
                # Exemple plus tard :
                # await send_mail(user["email"], "Votre post Clarity", caption, image_url)

                sent += 1

        return {"success": True, "sent": sent}

    except Exception as e:
        print(f"[SOCIAL CRON] error: {e}")
        return {"success": False, "message": str(e)}
