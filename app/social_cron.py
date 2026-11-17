import os
import json
import asyncio
import smtplib
from datetime import datetime
from zoneinfo import ZoneInfo
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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

def send_email(to_email: str, subject: str, body: str, html: bool = False) -> str:
    host = os.getenv("NOTIFY_SMTP_HOST") or os.getenv("SMTP_HOST")
    user = os.getenv("NOTIFY_SMTP_USER") or os.getenv("SMTP_USER")
    password = os.getenv("NOTIFY_SMTP_PASS") or os.getenv("SMTP_PASS")
    port = int(os.getenv("NOTIFY_SMTP_PORT") or os.getenv("SMTP_PORT") or "587")
    from_addr = os.getenv("NOTIFY_FROM") or user

    if not all([host, user, password, from_addr]):
        print("[SOCIAL CRON] SMTP incomplet")
        return "smtp incomplete"

    try:
        mime = MIMEMultipart()
        mime["Subject"] = subject
        mime["From"] = from_addr
        mime["To"] = to_email
        mime.attach(MIMEText(body, "html" if html else "plain", "utf-8"))

        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            s.login(user, password)
            s.sendmail(from_addr, [to_email], mime.as_string())
        print(f"[SOCIAL CRON] mail ok → {to_email}")
        return "ok"
    except Exception as e:
        print(f"[SOCIAL CRON] mail fail: {e}")
        return f"smtp: {e}"

def build_html(caption: str, image_url: str = None) -> str:
    img_html = ""
    if image_url:
        img_html = f'<img src="{image_url}" style="max-width:100%;border-radius:12px;margin-bottom:16px;" />'

    # On préserve les sauts de ligne du texte
    caption_html = caption.replace("\n", "<br>")

    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 520px; margin: 0 auto; padding: 20px; background: #fff; border-radius: 16px;">
      <p style="color: #666; font-size: 14px; margin: 0 0 16px;">Votre publication Clarity</p>
      {img_html}
      <div style="background: #f8f8f8; padding: 16px; border-radius: 12px; font-size: 15px; line-height: 1.5; white-space: pre-wrap;">
        {caption_html}
      </div>
      <p style="color: #999; font-size: 12px; margin-top: 20px;">Copiez-collez directement le texte ci-dessus.</p>
    </div>
    """

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
            # Comparaison stricte "schedule_time != current_time" ratait toute
            # heure programmee qui n'est pas un multiple exact de 5 minutes
            # (le cron ne s'execute qu'a :00/:05/:10...) : un utilisateur regle
            # sur 10h02 n'etait donc jamais detecte. On tolere desormais un
            # ecart de quelques minutes autour de l'heure choisie.
            try:
                sched_h, sched_m = map(int, schedule_time.split(":"))
                sched_minutes = sched_h * 60 + sched_m
                now_minutes = now.hour * 60 + now.minute
                diff = abs(now_minutes - sched_minutes)
                diff = min(diff, 1440 - diff)  # gere le passage minuit
            except Exception:
                diff = 9999
            if diff > 2:
                continue

            email = user.get("email")
            if not email:
                print(f"[SOCIAL CRON] pas d'email pour {user['id']}")
                continue

            async with httpx.AsyncClient() as client:
                start = await client.post(
                    "http://localhost:8000/social/generate",
                    json={
                        "user_id": user["id"],
                        "theme": "Conseil / astuce utile",
                        "platforms": ["instagram"],
                        "with_emojis": True,
                        "logo_url": user.get("social_logo_url")
                    },
                    timeout=30
                )
                if start.status_code != 200:
                    print(f"[SOCIAL CRON] gen fail {user['id']}")
                    continue
                start_data = start.json()
                job_id = start_data.get("job_id")
                if not start_data.get("success") or not job_id:
                    print(f"[SOCIAL CRON] pas de job_id pour {user['id']}")
                    continue

                # /social/generate renvoie désormais un job_id immédiatement
                # (génération en tâche de fond) : il faut interroger
                # /social/generate/status jusqu'à "done" avant d'avoir le
                # post. Avant ce correctif, le code lisait "posts" directement
                # sur la réponse du POST, qui ne l'a plus jamais contenu depuis
                # le passage au système de job_id — c'est ce qui empêchait
                # tout envoi de mail silencieusement.
                post = None
                for _ in range(40):  # ~40 x 2s = ~80s max d'attente
                    await asyncio.sleep(2)
                    status_resp = await client.get(
                        "http://localhost:8000/social/generate/status",
                        params={"job_id": job_id},
                        timeout=20
                    )
                    if status_resp.status_code != 200:
                        continue
                    status_data = status_resp.json()
                    if not status_data.get("success"):
                        continue
                    if status_data.get("status") == "done":
                        result_data = status_data.get("result") or {}
                        posts = result_data.get("posts") or []
                        if posts:
                            post = posts[0]
                        break
                    if status_data.get("status") == "error":
                        print(f"[SOCIAL CRON] génération en erreur pour {user['id']}: {status_data.get('error')}")
                        break

                if not post:
                    print(f"[SOCIAL CRON] pas de post généré pour {user['id']}")
                    continue

                caption = post.get("caption", "")
                image_url = post.get("image_url")

                html = build_html(caption, image_url)
                result = send_email(email, "Votre publication Clarity", html, html=True)

                if result == "ok":
                    sent += 1
                    print(f"[SOCIAL CRON] envoyé à {email}")

        return {"success": True, "sent": sent}

    except Exception as e:
        print(f"[SOCIAL CRON] error: {e}")
        return {"success": False, "message": str(e)}
