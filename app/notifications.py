"""
Notifications email : rappels à l'heure, RDV 1h avant.
Fuseau : Europe/Paris.
"""
from __future__ import annotations

import os
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

PARIS = ZoneInfo("Europe/Paris")
RDV_LEAD = timedelta(hours=1)
# Ne pas réveiller d'anciens rappels oubliés depuis des jours
MAX_LATENESS = timedelta(hours=2)


def get_supabase() -> Optional[Client]:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


def now_paris() -> datetime:
    return datetime.now(PARIS)


def parse_dt(date_s: Any, time_s: Any) -> Optional[datetime]:
    if not date_s:
        return None
    date_s = str(date_s)[:10]
    time_s = str(time_s or "08:00").strip()
    if len(time_s) >= 5:
        time_s = time_s[:5]
    try:
        dt = datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M")
        return dt.replace(tzinfo=PARIS)
    except Exception:
        return None


def lookup_email(sb: Client, user_id: str, cache: dict) -> Optional[str]:
    if not user_id:
        return None
    if user_id in cache:
        return cache[user_id]
    email = None
    try:
        r = sb.table("profiles").select("email").eq("id", user_id).limit(1).execute()
        if r.data and r.data[0].get("email"):
            email = r.data[0]["email"]
    except Exception as e:
        print(f"[Notify] profiles.id: {e}")
    if not email:
        try:
            r = sb.table("profiles").select("email").eq("user_id", user_id).limit(1).execute()
            if r.data and r.data[0].get("email"):
                email = r.data[0]["email"]
        except Exception as e:
            print(f"[Notify] profiles.user_id: {e}")
    cache[user_id] = email
    return email


def send_email(to_email: str, subject: str, body: str) -> bool:
    webhook = os.getenv("NOTIFY_WEBHOOK_URL")
    if webhook:
        try:
            requests.post(
                webhook,
                json={"to": to_email, "subject": subject, "body": body},
                timeout=20,
            )
            print(f"[Notify] webhook ok → {to_email} | {subject}")
            return True
        except Exception as e:
            print(f"[Notify] webhook fail: {e}")

    host = os.getenv("NOTIFY_SMTP_HOST") or os.getenv("SMTP_HOST")
    user = os.getenv("NOTIFY_SMTP_USER") or os.getenv("SMTP_USER")
    password = os.getenv("NOTIFY_SMTP_PASS") or os.getenv("SMTP_PASS")
    port = int(os.getenv("NOTIFY_SMTP_PORT") or os.getenv("SMTP_PORT") or "587")
    from_addr = os.getenv("NOTIFY_FROM") or user
    if host and user and password and from_addr:
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = subject
            msg["From"] = f"Clarity <{from_addr}>"
            msg["To"] = to_email
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls()
                s.login(user, password)
                s.sendmail(from_addr, [to_email], msg.as_string())
            print(f"[Notify] smtp ok → {to_email} | {subject}")
            return True
        except Exception as e:
            print(f"[Notify] smtp fail: {e}")

    print(f"[Notify] AUCUN ENVOI configuré. Would send to {to_email}: {subject}")
    return False


def mark_notified(sb: Client, table: str, row_id: str) -> None:
    try:
        sb.table(table).update({"notified_at": datetime.utcnow().isoformat() + "Z"}).eq("id", row_id).execute()
    except Exception as e:
        print(f"[Notify] mark {table} {row_id}: {e}")


def process_reminders(sb: Client, now: datetime, cache: dict) -> List[str]:
    sent = []
    try:
        q = sb.table("follow_ups").select("*").is_("notified_at", "null")
        rows = (q.limit(200).execute().data) or []
    except Exception as e:
        print(f"[Notify] load follow_ups: {e}")
        return sent

    for row in rows:
        status = (row.get("status") or "pending").lower()
        if status in ("done", "cancelled", "canceled"):
            continue
        when = parse_dt(row.get("reminder_date"), row.get("reminder_time"))
        if not when:
            continue
        if when > now:
            continue
        if now - when > MAX_LATENESS:
            continue
        email = lookup_email(sb, row.get("user_id") or "", cache)
        if not email:
            print(f"[Notify] pas d'email user={row.get('user_id')}")
            continue
        motif = (row.get("reason") or "Rappel").strip()
        motif = motif[0].upper() + motif[1:] if motif else "Rappel"
        heure = when.strftime("%d/%m/%Y à %H:%M")
        subject = f"Rappel Clarity : {motif}"
        body = (
            f"{motif}\n\n"
            f"{heure}\n"
            + (f"Contact : {row.get('contact_name')}\n" if row.get("contact_name") and row.get("contact_name") != "Moi" else "")
            + "\n— Clarity"
        )
        if send_email(email, subject, body):
            mark_notified(sb, "follow_ups", row["id"])
            sent.append(f"rappel:{motif}")
    return sent


def process_appointments(sb: Client, now: datetime, cache: dict) -> List[str]:
    sent = []
    try:
        q = sb.table("appointments").select("*").is_("notified_at", "null")
        rows = (q.limit(200).execute().data) or []
    except Exception as e:
        print(f"[Notify] load appointments: {e}")
        return sent

    for row in rows:
        status = (row.get("status") or "scheduled").lower()
        if status in ("done", "cancelled", "canceled"):
            continue
        when = parse_dt(row.get("appointment_date"), row.get("appointment_time"))
        if not when:
            continue
        notify_at = when - RDV_LEAD
        if now < notify_at:
            continue
        if now - when > timedelta(minutes=20):
            continue
        email = lookup_email(sb, row.get("user_id") or "", cache)
        if not email:
            print(f"[Notify] pas d'email user={row.get('user_id')}")
            continue
        motif = (row.get("title") or row.get("description") or "Rendez-vous").strip()
        who = (row.get("contact_name") or "").strip()
        heure = when.strftime("%d/%m/%Y à %H:%M")
        subject = f"RDV Clarity dans 1 h : {motif}"
        lines = [motif, "", heure]
        if who:
            lines.append(f"Avec : {who}")
        if row.get("description") and row.get("description") != motif:
            lines.append(row["description"])
        lines += ["", "— Clarity"]
        if send_email(email, subject, "\n".join(lines)):
            mark_notified(sb, "appointments", row["id"])
            sent.append(f"rdv:{motif}")
    return sent


def run_notification_pass() -> dict:
    sb = get_supabase()
    if not sb:
        return {"ok": False, "error": "Supabase manquant"}
    now = now_paris()
    cache: dict = {}
    rappels = process_reminders(sb, now, cache)
    rdvs = process_appointments(sb, now, cache)
    print(f"[Notify] pass {now.isoformat()} rappels={rappels} rdvs={rdvs}")
    return {
        "ok": True,
        "now": now.isoformat(),
        "reminders_sent": rappels,
        "appointments_sent": rdvs,
    }
