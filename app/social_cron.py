import os
import json
import asyncio
import base64
import smtplib
from datetime import datetime
from zoneinfo import ZoneInfo
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from fastapi import APIRouter
from supabase import create_client
import httpx

# Reutilise la logique de notifications.py (rappels/RDV, qui fonctionnent
# deja) : si profiles.email est vide, elle va chercher l'email directement
# dans Supabase Auth (auth.users) via sb.auth.admin.get_user_by_id. C'est
# exactement ce qui manquait ici : social_cron.py se contentait de lire
# profiles.email et abandonnait silencieusement quand elle etait vide.
from app.notifications import lookup_email

router = APIRouter()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

PARIS = ZoneInfo("Europe/Paris")

# Le frontend (dashboard-67.html) enregistre les jours de la semaine en
# anglais ("mon", "tue", "wed", "thu", "fri", "sat", "sun") dans
# social_schedule_days. DAY_MAP utilisait auparavant des cles francaises
# ("lun", "mar", ...), qui ne correspondaient donc JAMAIS aux cles reellement
# enregistrees : aucune publication n'etait jamais detectee, quel que soit le
# jour ou l'heure choisis. C'est la cause racine de l'absence totale de mail.
DAY_MAP = {
    0: "mon",
    1: "tue",
    2: "wed",
    3: "thu",
    4: "fri",
    5: "sat",
    6: "sun"
}

def send_email(to_email: str, subject: str, body: str, html: bool = False, inline_images: list = None) -> str:
    """inline_images : liste de (cid, data_uri) a joindre en Content-ID
    (data_uri au format "data:image/jpeg;base64,xxxx"), referencees dans le
    HTML par <img src="cid:LE_CID">. Plus fiable que <img src="data:..."">
    brut, mal supporte par certains clients mail (ex. Outlook)."""
    host = os.getenv("NOTIFY_SMTP_HOST") or os.getenv("SMTP_HOST")
    user = os.getenv("NOTIFY_SMTP_USER") or os.getenv("SMTP_USER")
    password = os.getenv("NOTIFY_SMTP_PASS") or os.getenv("SMTP_PASS")
    port = int(os.getenv("NOTIFY_SMTP_PORT") or os.getenv("SMTP_PORT") or "587")
    from_addr = os.getenv("NOTIFY_FROM") or user

    if not all([host, user, password, from_addr]):
        print("[SOCIAL CRON] SMTP incomplet")
        return "smtp incomplete"

    try:
        mime = MIMEMultipart("related")
        mime["Subject"] = subject
        mime["From"] = from_addr
        mime["To"] = to_email
        mime.attach(MIMEText(body, "html" if html else "plain", "utf-8"))

        for cid, data_uri in (inline_images or []):
            try:
                header, b64data = data_uri.split(",", 1)
                subtype = "jpeg"
                if "image/png" in header:
                    subtype = "png"
                img_bytes = base64.b64decode(b64data)
                img_part = MIMEImage(img_bytes, _subtype=subtype)
                img_part.add_header("Content-ID", f"<{cid}>")
                img_part.add_header("Content-Disposition", "inline", filename=f"{cid}.{subtype}")
                mime.attach(img_part)
            except Exception as e:
                print(f"[SOCIAL CRON] image inline {cid} ignoree: {e}")

        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            s.login(user, password)
            s.sendmail(from_addr, [to_email], mime.as_string())
        print(f"[SOCIAL CRON] mail ok → {to_email}")
        return "ok"
    except Exception as e:
        print(f"[SOCIAL CRON] mail fail: {e}")
        return f"smtp: {e}"

PLATFORM_LABELS_CRON = {
    "instagram": "Instagram",
    "facebook": "Facebook",
    "linkedin": "LinkedIn"
}

def build_section_html(post: dict, img_cid: str = None) -> str:
    caption = (post.get("caption") or "").replace("\n", "<br>")
    first_comment = (post.get("first_comment") or "").replace("\n", "<br>")
    label = PLATFORM_LABELS_CRON.get(post.get("platform"), post.get("platform") or "")

    img_html = ""
    if img_cid:
        img_html = f'<img src="cid:{img_cid}" style="max-width:100%;border-radius:12px;margin:12px 0;" />'

    comment_html = ""
    if first_comment:
        comment_html = f"""
      <p style="color:#888; font-size:12px; margin:12px 0 4px; text-transform:uppercase; letter-spacing:0.04em;">1er commentaire</p>
      <div style="background:#f0f0f0; padding:12px 16px; border-radius:10px; font-size:14px; line-height:1.5; white-space:pre-wrap;">
        {first_comment}
      </div>"""

    return f"""
    <div style="margin-bottom:28px; padding-bottom:28px; border-bottom:1px solid #eee;">
      <p style="color:#6b4ee6; font-size:13px; font-weight:600; margin:0 0 10px; text-transform:uppercase; letter-spacing:0.04em;">{label}</p>
      <div style="background:#f8f8f8; padding:16px; border-radius:12px; font-size:15px; line-height:1.5; white-space:pre-wrap;">
        {caption}
      </div>
      {img_html}
      {comment_html}
    </div>
    """

def build_html(posts: list, images_by_platform: dict) -> str:
    """posts : liste d'objets {platform, caption, first_comment, image_url}.
    images_by_platform : { platform: cid } pour les images deja jointes en
    inline (voir send_email/inline_images)."""
    sections = "".join(
        build_section_html(post, images_by_platform.get(post.get("platform")))
        for post in posts
    )
    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 560px; margin: 0 auto; padding: 24px; background: #fff; border-radius: 16px;">
      <p style="color: #666; font-size: 14px; margin: 0 0 20px;">Vos publications Clarity du jour</p>
      {sections}
      <p style="color: #999; font-size: 12px; margin-top: 8px;">Copiez-collez directement le texte de chaque réseau ci-dessus.</p>
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
            "id, email, social_context, social_length, social_logo_url, social_schedule_days"
        ).execute()

        users = res.data or []
        sent = 0
        email_cache: dict = {}

        for user in users:
            # social_schedule_days est un objet { "lun": "07:00", "mar": "20:10", ... } :
            # une heure PAR jour, pas une liste de jours + une heure globale
            # separee. Le frontend n'ecrit jamais dans une colonne
            # "social_schedule_time" (verifie dans dashboard-67.html) : lire
            # cette colonne-la donnait toujours le defaut "07:00", quel que
            # soit l'horaire choisi par l'utilisateur pour ce jour precis.
            days_raw = user.get("social_schedule_days") or {}
            if isinstance(days_raw, str):
                try:
                    days_raw = json.loads(days_raw)
                except Exception:
                    days_raw = {}
            if not isinstance(days_raw, dict):
                days_raw = {}

            schedule_time = days_raw.get(current_day)
            if not schedule_time:
                continue  # pas de programmation pour aujourd'hui

            # Comparaison stricte ratait toute heure programmee qui n'est pas
            # un multiple exact de 5 minutes (le cron ne s'execute qu'a
            # :00/:05/:10...) : un utilisateur regle sur 10h02 n'etait donc
            # jamais detecte. On tolere desormais un ecart de quelques
            # minutes autour de l'heure choisie.
            try:
                sched_h, sched_m = map(int, str(schedule_time).split(":"))
                sched_minutes = sched_h * 60 + sched_m
                now_minutes = now.hour * 60 + now.minute
                diff = abs(now_minutes - sched_minutes)
                diff = min(diff, 1440 - diff)  # gere le passage minuit
            except Exception:
                diff = 9999
            if diff > 2:
                continue

            email = user.get("email") or lookup_email(supabase, str(user["id"]), email_cache)
            if not email:
                print(f"[SOCIAL CRON] pas d'email pour {user['id']}")
                continue

            async with httpx.AsyncClient() as client:
                # En production (Render), l'app écoute sur le port fourni par
                # $PORT, pas sur 8000 : l'appel interne codé en dur sur
                # localhost:8000 échouait silencieusement (connexion refusée)
                # à chaque exécution du cron, donc aucune génération ne
                # démarrait jamais et aucun mail ne partait.
                internal_port = os.getenv("PORT", "8000")
                start = await client.post(
                    f"http://localhost:{internal_port}/social/generate",
                    json={
                        "user_id": user["id"],
                        "theme": "Conseil / astuce utile",
                        # Les 3 reseaux geres par Clarity (voir SOCIAL_PLATFORMS
                        # cote frontend) : avant, seul Instagram etait demande,
                        # donc le mail ne contenait jamais Facebook/LinkedIn.
                        "platforms": ["instagram", "facebook", "linkedin"],
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
                posts = []
                # 3 reseaux a generer (texte + image chacun) au lieu d'un
                # seul avant : on elargit la fenetre d'attente du polling
                # par securite (60 x 2s = 120s max, contre 80s avant).
                for _ in range(60):
                    await asyncio.sleep(2)
                    status_resp = await client.get(
                        f"http://localhost:{internal_port}/social/generate/status",
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
                        break
                    if status_data.get("status") == "error":
                        print(f"[SOCIAL CRON] génération en erreur pour {user['id']}: {status_data.get('error')}")
                        break

                if not posts:
                    print(f"[SOCIAL CRON] pas de post généré pour {user['id']}")
                    continue

                # Chaque image est une data:image/...;base64,... (jamais une
                # URL classique, voir generate_image dans social_posts.py) :
                # on la joint en piece jointe inline (Content-ID) plutot que
                # de la mettre brute dans <img src="data:...">, plus fiable
                # selon les clients mail.
                inline_images = []
                images_by_platform = {}
                for idx, post in enumerate(posts):
                    image_url = post.get("image_url")
                    if image_url and image_url.startswith("data:image"):
                        cid = f"socialimg{idx}"
                        inline_images.append((cid, image_url))
                        images_by_platform[post.get("platform")] = cid

                html = build_html(posts, images_by_platform)
                result = send_email(
                    email,
                    "Vos publications Clarity du jour",
                    html,
                    html=True,
                    inline_images=inline_images
                )

                if result == "ok":
                    sent += 1
                    print(f"[SOCIAL CRON] envoyé à {email}")

        return {"success": True, "sent": sent}

    except Exception as e:
        print(f"[SOCIAL CRON] error: {e}")
        return {"success": False, "message": str(e)}
