"""Envoi mail natif SMTP + Gmail. Make reste le chemin prod sans flag."""

import base64
import os
import smtplib
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from typing import List, Optional

import httpx
from supabase import create_client


def _sb():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


def _rows(user_id: str) -> list:
    sb = _sb()
    if not sb or not user_id:
        return []
    res = sb.table("user_integrations").select("*").eq("user_id", user_id).execute()
    return res.data or []


def _pick(rows: list, via: str) -> Optional[dict]:
    via = (via or "").lower()
    google = [r for r in rows if (r.get("provider") or "").lower() == "google"]
    smtp = [r for r in rows if (r.get("provider") or "").lower() == "smtp" and r.get("smtp_host")]
    live = [r for r in rows if r.get("connected") is True]
    if via in ("gmail", "google"):
        return next((r for r in live if (r.get("provider") or "").lower() == "google"), None) or (google[0] if google else None)
    if via == "smtp":
        return smtp[0] if smtp else None
    if live:
        return live[0]
    if google:
        return google[0]
    if smtp:
        return smtp[0]
    return rows[0] if rows else None


def _build_mime(from_addr, to_addr, to_name, subject, body, attachments) -> MIMEMultipart:
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
    return msg


def _refresh_google(refresh_token: str) -> Optional[str]:
    cid = os.getenv("GOOGLE_CLIENT_ID") or os.getenv("GMAIL_CLIENT_ID")
    csec = os.getenv("GOOGLE_CLIENT_SECRET") or os.getenv("GMAIL_CLIENT_SECRET")
    if not (cid and csec and refresh_token):
        print("[MailSend] Google client id/secret manquant")
        return None
    try:
        r = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": cid,
                "client_secret": csec,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=20,
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            print(f"[MailSend] refresh google {r.status_code} {data}")
            return None
        return data.get("access_token")
    except Exception as e:
        print(f"[MailSend] refresh google err: {e}")
        return None


def _save_access(row_id, token: str) -> None:
    sb = _sb()
    if not sb or not row_id or not token:
        return
    try:
        sb.table("user_integrations").update({"access_token": token}).eq("id", row_id).execute()
    except Exception as e:
        print(f"[MailSend] save token: {e}")


def _gmail_send(token: str, raw_b64: str) -> dict:
    r = httpx.post(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"raw": raw_b64},
        timeout=30,
    )
    if r.status_code in (200, 202):
        return {"ok": True}
    txt = (r.text or "")[:300]
    print(f"[MailSend] gmail send {r.status_code} {txt}")
    low = txt.lower()
    if r.status_code in (401, 403) or "insufficient" in low or "invalid_grant" in low:
        return {"ok": False, "reauth": True}
    return {"ok": False, "reauth": False}


def send_user_mail(payload: dict) -> dict:
    user_id = (payload.get("user_id") or "").strip()
    to_addr = (payload.get("to") or payload.get("to_email") or "").strip()
    subject = (payload.get("subject") or "").strip() or "(sans objet)"
    body = payload.get("html") or payload.get("body") or payload.get("message") or ""
    to_name = (payload.get("to_name") or "").strip()
    attachments = payload.get("attachments") or []
    via = (payload.get("via") or payload.get("provider") or "").strip()

    if not user_id:
        return {"success": False, "reason": "reauth_required", "message": "Compte introuvable."}
    if not to_addr or "@" not in to_addr:
        return {"success": False, "reason": "no_email", "message": "Aucune adresse e-mail pour ce contact."}

    integ = _pick(_rows(user_id), via)
    if not integ:
        return {
            "success": False,
            "reason": "reauth_required",
            "message": "Reconnectez votre boîte dans Réglages.",
        }

    provider = (integ.get("provider") or "").lower()

    if provider == "google":
        from_addr = (integ.get("email") or integ.get("account_email") or "").strip()
        if not from_addr:
            from_addr = to_addr
        msg = _build_mime(from_addr, to_addr, to_name, subject, body, attachments)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
        token = (integ.get("access_token") or "").strip()
        refresh = (integ.get("refresh_token") or "").strip()
        if not token and refresh:
            token = _refresh_google(refresh) or ""
            if token:
                _save_access(integ.get("id"), token)
        if not token:
            return {
                "success": False,
                "reason": "reauth_required",
                "message": "Reconnectez Gmail dans Réglages.",
            }
        out = _gmail_send(token, raw)
        if not out.get("ok") and refresh:
            token = _refresh_google(refresh) or ""
            if token:
                _save_access(integ.get("id"), token)
                out = _gmail_send(token, raw)
        if out.get("ok"):
            return {"success": True, "via": "gmail", "message": "Mail envoyé."}
        return {
            "success": False,
            "reason": "reauth_required" if out.get("reauth") else "send_failed",
            "message": "Reconnectez Gmail dans Réglages." if out.get("reauth") else "L'envoi Gmail a échoué.",
        }

    host = (integ.get("smtp_host") or "").strip()
    if provider != "smtp" or not host:
        return {
            "success": False,
            "reason": "use_make",
            "message": "Cette boîte n'est pas encore en envoi natif.",
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
    msg = _build_mime(user, to_addr, to_name, subject, body, attachments)
    try:
        with smtplib.SMTP(host, port, timeout=25) as s:
            s.starttls()
            s.login(user, password)
            s.sendmail(user, [to_addr], msg.as_string())
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
