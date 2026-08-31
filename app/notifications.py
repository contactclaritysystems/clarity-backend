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

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

PARIS = ZoneInfo("Europe/Paris")
RDV_LEAD = timedelta(hours=1)
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

    # profiles.id = auth user id (schéma Clarity)
    for col_filter in ("id", "user_id"):
        try:
            r = sb.table("profiles").select("*").eq(col_filter, user_id).limit(1).execute()
            if r.data:
                row = r.data[0]
                email = row.get("email") or row.get("user_email") or row.get("mail")
                if email:
                    break
        except Exception as e:
            print(f"[Notify] profiles.{col_filter}: {e}")

    if not email:
        try:
            au = sb.auth.admin.get_user_by_id(user_id)
            user = getattr(au, "user", None) or au
            email = getattr(user, "email", None)
            if not email and isinstance(user, dict):
                email = user.get("email")
        except Exception as e:
            print(f"[Notify] auth.admin: {e}")

    cache[user_id] = email
    print(f"[Notify] email for {user_id} = {email}")
    return email


def send_email(to_email: str, subject: str, body: str) -> str:
    """Retourne 'ok' ou le message d'erreur."""
    host = os.getenv("NOTIFY_SMTP_HOST") or os.getenv("SMTP_HOST")
    user = os.getenv("NOTIFY_SMTP_USER") or os.getenv("SMTP_USER")
    password = os.getenv("NOTIFY_SMTP_PASS") or os.getenv("SMTP_PASS")
    port = int(os.getenv("NOTIFY_SMTP_PORT") or os.getenv("SMTP_PORT") or "587")
    from_addr = os.getenv("NOTIFY_FROM") or user

    webhook = os.getenv("NOTIFY_WEBHOOK_URL")
    if webhook:
        try:
            import json as _json
            from urllib.request import Request, urlopen
            req = Request(
                webhook,
                data=_json.dumps({"to": to_email, "subject": subject, "body": body}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urlopen(req, timeout=20).read()
            print(f"[Notify] webhook ok → {to_email}")
            return "ok"
        except Exception as e:
            print(f"[Notify] webhook fail: {e}")
            return f"webhook: {e}"

    if not (host and user and password and from_addr):
        msg = "SMTP incomplet (HOST/USER/PASS/FROM)"
        print(f"[Notify] {msg}")
        return msg
    try:
        mime = MIMEText(body, "plain", "utf-8")
        mime["Subject"] = subject
        mime["From"] = f"Clarity <{from_addr}>"
        mime["To"] = to_email
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            s.login(user, password)
            s.sendmail(from_addr, [to_email], mime.as_string())
        print(f"[Notify] smtp ok → {to_email} | {subject}")
        return "ok"
    except Exception as e:
        print(f"[Notify] smtp fail: {e}")
        return f"smtp: {e}"


def mark_notified(sb: Client, table: str, row_id: str) -> None:
    try:
        sb.table(table).update({"notified_at": datetime.utcnow().isoformat() + "Z"}).eq("id", row_id).execute()
    except Exception as e:
        print(f"[Notify] mark {table} {row_id}: {e}")


def load_rows(sb: Client, table: str) -> List[dict]:
    try:
        r = sb.table(table).select("*").limit(300).execute()
        return r.data or []
    except Exception as e:
        print(f"[Notify] load {table}: {e}")
        return []


def process_reminders(sb: Client, now: datetime, cache: dict, debug: list) -> List[str]:
    sent = []
    rows = load_rows(sb, "follow_ups")
    debug.append(f"follow_ups_total={len(rows)}")
    for row in rows:
        if row.get("notified_at"):
            continue
        status = (row.get("status") or "pending").lower()
        if status in ("done", "cancelled", "canceled"):
            continue
        when = parse_dt(row.get("reminder_date"), row.get("reminder_time"))
        if not when:
            debug.append(f"skip no-date id={row.get('id')}")
            continue
        if when > now:
            continue
        if now - when > MAX_LATENESS:
            continue
        email = lookup_email(sb, str(row.get("user_id") or ""), cache)
        motif = (row.get("reason") or "Rappel").strip() or "Rappel"
        motif = motif[0].upper() + motif[1:]
        heure = when.strftime("%d/%m/%Y à %H:%M")
        if not email:
            debug.append(f"no-email user={row.get('user_id')} {motif}")
            continue
        subject = f"Rappel Clarity : {motif}"
        body = f"{motif}\n\n{heure}\n\n— Clarity"
        result = send_email(email, subject, body)
        if result == "ok":
            mark_notified(sb, "follow_ups", row["id"])
            sent.append(f"{motif} → {email}")
        else:
            debug.append(f"send-fail {motif}: {result}")
    return sent


def process_appointments(sb: Client, now: datetime, cache: dict, debug: list) -> List[str]:
    sent = []
    rows = load_rows(sb, "appointments")
    debug.append(f"appointments_total={len(rows)}")
    for row in rows:
        if row.get("notified_at"):
            continue
        status = (row.get("status") or "scheduled").lower()
        if status in ("done", "cancelled", "canceled"):
            continue
        when = parse_dt(row.get("appointment_date"), row.get("appointment_time"))
        if not when:
            continue
        if now < when - RDV_LEAD:
            continue
        if now - when > timedelta(minutes=20):
            continue
        email = lookup_email(sb, str(row.get("user_id") or ""), cache)
        motif = (row.get("title") or "Rendez-vous").strip()
        if not email:
            debug.append(f"no-email rdv user={row.get('user_id')}")
            continue
        heure = when.strftime("%d/%m/%Y à %H:%M")
        who = (row.get("contact_name") or "").strip()
        subject = f"RDV Clarity dans 1 h : {motif}"
        body = f"{motif}\n\n{heure}" + (f"\nAvec : {who}" if who else "") + "\n\n— Clarity"
        result = send_email(email, subject, body)
        if result == "ok":
            mark_notified(sb, "appointments", row["id"])
            sent.append(f"{motif} → {email}")
        else:
            debug.append(f"send-fail rdv {motif}: {result}")
    return sent


def run_notification_pass() -> dict:
    sb = get_supabase()
    if not sb:
        return {"ok": False, "error": "Supabase manquant"}
    now = now_paris()
    cache: dict = {}
    debug: list = []
    rappels = process_reminders(sb, now, cache, debug)
    rdvs = process_appointments(sb, now, cache, debug)
    print(f"[Notify] pass {now.isoformat()} rappels={rappels} rdvs={rdvs} debug={debug}")
    return {
        "ok": True,
        "now": now.isoformat(),
        "reminders_sent": rappels,
        "appointments_sent": rdvs,
        "debug": debug[-20:],
    }
