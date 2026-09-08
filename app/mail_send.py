"""Envoi mail natif (SMTP user_integrations). Make reste le chemin prod."""

import base64
import os
import smtplib
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from typing import Any, Dict, List, Optional

from supabase import create_client


def _sb():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


def _integration(user_id: str) -> Optional[dict]:
    sb = _sb()
    if not sb or not user_id:
        return None
    res = (
        sb.table("user_integrations")
        .select("*")
        .eq("user_id", user_id)
        .execute()
    )
    rows = res.data or []
    smtp = [r for r in rows if (r.get("provider") or "").lower() == "smtp" and r.get("smtp_host")]
    if smtp:
        return smtp[0]
    live = [r for r in rows if r.get("connected") is True]
    return live[0] if live else (rows[0] if rows else None)


def send_user_mail(payload: dict) -> dict:
    user_id = (payload.get("user_id") or "").strip()
    to_addr = (payload.get("to") or payload.get("to_email") or "").strip()
    subject = (payload.get("subject") or "").strip() or "(sans objet)"
    body = payload.get("html") or payload.get("body") or payload.get("message") or ""
    to_name = (payload.get("to_name") or "").strip()
    attachments = payload.get("attachments") or []

    if not user_id:
        return {"success": False, "reason": "reauth_required", "message": "Compte introuvable."}
    if not to_addr or "@" not in to_addr:
        return {"success": False, "reason": "no_email", "message": "Aucune adresse e-mail pour ce contact."}

    integ = _integration(user_id)
    if not integ:
        return {
            "success": False,
            "reason": "reauth_required",
            "message": "Reconnectez votre boîte dans Réglages.",
        }

    provider = (integ.get("provider") or "").lower()
    host = (integ.get("smtp_host") or "").strip()
    if provider != "smtp" or not host:
        return {
            "success": False,
            "reason": "use_make",
            "message": "Gmail/Outlook : envoi Make pour l'instant. SMTP disponible en test.",
        }

    port = int(integ.get("smtp_port") or 587)
    user = (integ.get("email") or integ.get("account_email") or "").strip()
    password = integ.get("smtp_password") or ""
    if not user or not password:
        return {
            "success": False,
            "reason": "reauth_required",
            "message": "Reconnectez SMTP dans Réglages.",
        }

    from_addr = user
    html = body if "<" in str(body) else str(body).replace("\n", "<br>\n")

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = f"{to_name} <{to_addr}>" if to_name else to_addr
    msg.attach(MIMEText(html, "html", "utf-8"))

    for att in attachments if isinstance(attachments, list) else []:
        if not isinstance(att, dict):
            continue
        raw = att.get("content_b64") or att.get("data") or ""
        name = att.get("filename") or att.get("name") or "fichier"
        mime = att.get("mime") or "application/octet-stream"
        if not raw:
            continue
        try:
            data = base64.b64decode(raw)
        except Exception:
            continue
        main, sub = (mime.split("/", 1) + ["octet-stream"])[:2]
        part = MIMEBase(main, sub)
        part.set_payload(data)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=name)
        msg.attach(part)

    try:
        with smtplib.SMTP(host, port, timeout=25) as s:
            s.starttls()
            s.login(user, password)
            s.sendmail(from_addr, [to_addr], msg.as_string())
    except Exception as e:
        err = str(e).lower()
        if "auth" in err or "login" in err or "credential" in err:
            return {
                "success": False,
                "reason": "reauth_required",
                "message": "Reconnectez SMTP dans Réglages.",
            }
        return {"success": False, "message": "L'envoi a échoué. Réessayez."}

    return {"success": True, "via": "smtp", "message": "Mail envoyé."}
