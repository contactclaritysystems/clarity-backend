import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter
from supabase import create_client
import httpx
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

router = APIRouter()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

PARIS = ZoneInfo("Europe/Paris")

DAY_MAP = {
    0: "mon",
    1: "tue",
    2: "wed",
    3: "thu",
    4: "fri",
    5: "sat",
    6: "sun"
}

SMTP_HOST = os.getenv("NOTIFY_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("NOTIFY_SMTP_PORT", "587"))
SMTP_USER = os.getenv("NOTIFY_SMTP_USER")
SMTP_PASS = os.getenv("NOTIFY_SMTP_PASS")
SMTP_FROM = os.getenv("NOTIFY_FROM", SMTP_USER)

def send_post_email(to_email: str, caption: str, image_url: str = None):
    if not SMTP_USER or not SMTP_PASS:
        print("[SOCIAL CRON] SMTP non configure")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Votre post Instagram est pret"
    msg["From"] = SMTP_FROM
    msg["To"] = to_email

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
      <h2 style="color:#1a1a1a;">Votre post est pret</h2>
      <p style="white-space:pre-wrap;line-height:1.5;">{caption}</p>
      {f'<img src="{image_url}" style="max-width:100%;border-radius:12px;margin-top:12px;" />' if image_url else ''}
      <p style="color:#666;font-size:13px;margin-top:20px;">Copiez-collez directement dans Instagram.</p>
    </div>
    """
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_FROM, to_email, msg.as_string())
        print(f"[SOCIAL CRON] Mail envoye a {to_email}")
        return True
    except Exception as e:
        print(f"[SOCIAL CRON] Erreur mail: {e}")
        return False


@router.post("/cron/social-posts")
async def send_scheduled_posts():
    if not supabase:
        return {"success": False, "message": "Supabase non configure"}

    now = datetime.now(PARIS)
    current_day = DAY_MAP[now.weekday()]
    current_time = now.strftime("%H:%M")

    print(f"[SOCIAL CRON] {current_day} {current_time}")

    try:
        res = supabase.table("profiles").select(
            "id, email, social_context, social_length, social_logo_url, social_schedule_days"
        ).execute()

        users = res.data or []
        sent = 0

        for user in users:
            schedule = user.get("social_schedule_days") or {}
            if isinstance(schedule, str):
                try:
                    schedule = json.loads(schedule)
                except:
                    schedule = {}

            if not isinstance(schedule, dict):
                continue

            scheduled_time = schedule.get(current_day)
            if not scheduled_time or scheduled_time != current_time:
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

                email = user.get("email")
                if email and send_post_email(email, caption, image_url):
                    sent += 1

        return {"success": True, "sent": sent}

    except Exception as e:
        print(f"[SOCIAL CRON] error: {e}")
        return {"success": False, "message": str(e)}
