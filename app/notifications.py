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


def user_offsets(sb: Client, user_id: str, cache: dict) -> dict:
    key = f"off:{user_id}"
    if key in cache:
        return cache[key]
    rem, appt = "0", "60"
    prof = get_profile(sb, user_id) if user_id else None
    if prof:
        rem = str(prof.get("reminder_offset") or "0")
        appt = str(prof.get("appointment_offset") or "60")
    out = {"reminder": rem, "appointment": appt}
    cache[key] = out
    return out


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
        off = user_offsets(sb, str(row.get("user_id") or ""), cache)
        if off["reminder"] in ("off", "none", "false", "digest"):
            continue
        try:
            lead_min = int(off["reminder"])
        except Exception:
            lead_min = 0
        when = parse_dt(row.get("reminder_date"), row.get("reminder_time"))
        if not when:
            debug.append(f"skip no-date id={row.get('id')}")
            continue
        start = when - timedelta(minutes=lead_min)
        if now < start:
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
        off = user_offsets(sb, str(row.get("user_id") or ""), cache)
        if off["appointment"] in ("off", "none", "false", "digest"):
            continue
        try:
            lead_min = int(off["appointment"])
        except Exception:
            lead_min = 60
        if now < when - timedelta(minutes=lead_min):
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
        if lead_min <= 0:
            lead_txt = "maintenant"
        elif lead_min < 60:
            lead_txt = f"dans {lead_min} min"
        else:
            h, m = divmod(lead_min, 60)
            lead_txt = f"dans {h} h" + (f" {m:02d}" if m else "")
        subject = f"RDV Clarity {lead_txt} : {motif}"
        body = f"{motif}\n\n{heure}" + (f"\nAvec : {who}" if who else "") + "\n\n— Clarity"
        result = send_email(email, subject, body)
        if result == "ok":
            mark_notified(sb, "appointments", row["id"])
            sent.append(f"{motif} → {email}")
        else:
            debug.append(f"send-fail rdv {motif}: {result}")
    return sent




def get_profile(sb: Client, user_id: str) -> Optional[dict]:
    try:
        r = sb.table("profiles").select("*").eq("id", user_id).limit(1).execute()
        if r.data:
            return r.data[0]
    except Exception as e:
        print(f"[Notify] get_profile: {e}")
    return None


def save_digest_settings(user_id: str, enabled: Optional[bool], time_s: Optional[str], user_email: Optional[str] = None, reminder_offset: Optional[str] = None, appointment_offset: Optional[str] = None) -> dict:
    sb = get_supabase()
    if not sb or not user_id:
        return {"success": False, "message": "Paramètres incomplets."}
    patch = {}
    if enabled is not None:
        patch["digest_enabled"] = bool(enabled)
    if time_s:
        time_s = str(time_s).strip()[:5]
        try:
            datetime.strptime(time_s, "%H:%M")
        except Exception:
            return {"success": False, "message": "Heure invalide (HH:MM)."}
        patch["digest_time"] = time_s
    def _norm_offset(val: str) -> Optional[str]:
        v = str(val or "").strip().lower()
        if v in ("off", "none", "false"):
            return "off"
        if v in ("digest", "brief"):
            return "digest"
        try:
            n = int(v)
        except Exception:
            return None
        if n < 0 or n > 10080:
            return None
        return str(n)
    if reminder_offset is not None:
        n = _norm_offset(reminder_offset)
        if n is None:
            return {"success": False, "message": "reminder_offset invalide"}
        patch["reminder_offset"] = n
    if appointment_offset is not None:
        n = _norm_offset(appointment_offset)
        if n is None:
            return {"success": False, "message": "appointment_offset invalide"}
        patch["appointment_offset"] = n
    if not patch:
        s = get_digest_settings(user_id)
        return {"success": True, "settings": s, **s}
    err = None
    row = None
    try:
        r = sb.table("profiles").update(patch).eq("id", user_id).execute()
        if r.data:
            row = r.data[0]
    except Exception as e:
        err = str(e)
        print(f"[Notify] digest update id: {e}")
    if not row and user_email:
        try:
            r = sb.table("profiles").update(patch).eq("email", user_email).execute()
            if r.data:
                row = r.data[0]
        except Exception as e:
            err = str(e)
            print(f"[Notify] digest update email: {e}")
    if not row:
        insert_row = {"id": user_id, **patch}
        if user_email:
            insert_row["email"] = user_email
        try:
            r = sb.table("profiles").insert(insert_row).execute()
            if r.data:
                row = r.data[0]
        except Exception as e:
            err = str(e)
            print(f"[Notify] digest insert: {e}")
            insert_row.pop("email", None)
            try:
                r = sb.table("profiles").insert(insert_row).execute()
                if r.data:
                    row = r.data[0]
            except Exception as e2:
                err = str(e2)
                print(f"[Notify] digest insert2: {e2}")
    if row:
        s = {
            "digest_enabled": row.get("digest_enabled", True),
            "digest_time": str(row.get("digest_time") or time_s or "07:30")[:5],
            "digest_last_sent": None,
            "reminder_offset": str(row.get("reminder_offset") or "0"),
            "appointment_offset": str(row.get("appointment_offset") or "60"),
        }
        return {"success": True, "settings": s, **s}
    return {
        "success": False,
        "message": "Impossible d'enregistrer. Envoie le SELECT id, email FROM profiles.",
        "error": err,
    }


def get_digest_settings(user_id: str) -> dict:
    sb = get_supabase()
    prof = get_profile(sb, user_id) if sb else None
    enabled = True
    time_s = "07:30"
    last = None
    if prof:
        if prof.get("digest_enabled") is False:
            enabled = False
        if prof.get("digest_time"):
            time_s = str(prof["digest_time"])[:5]
        last = prof.get("digest_last_sent")
    rem, appt = "0", "60"
    if prof:
        rem = str(prof.get("reminder_offset") or "0")
        appt = str(prof.get("appointment_offset") or "60")
    return {
        "digest_enabled": enabled,
        "digest_time": time_s,
        "digest_last_sent": str(last)[:10] if last else None,
        "reminder_offset": rem,
        "appointment_offset": appt,
    }


def day_items(sb: Client, user_id: str, day: str) -> tuple:
    rappels, rdvs = [], []
    try:
        rows = (
            sb.table("follow_ups")
            .select("*")
            .eq("user_id", user_id)
            .eq("reminder_date", day)
            .limit(50)
            .execute()
            .data
            or []
        )
        for r in rows:
            st = (r.get("status") or "pending").lower()
            if st in ("done", "cancelled", "canceled"):
                continue
            hh = str(r.get("reminder_time") or "")[:5]
            motif = r.get("reason") or "Rappel"
            rappels.append((hh or "??:??", motif, r.get("contact_name") or ""))
    except Exception as e:
        print(f"[Notify] digest follow_ups: {e}")
    try:
        rows = (
            sb.table("appointments")
            .select("*")
            .eq("user_id", user_id)
            .eq("appointment_date", day)
            .limit(50)
            .execute()
            .data
            or []
        )
        for r in rows:
            st = (r.get("status") or "scheduled").lower()
            if st in ("done", "cancelled", "canceled"):
                continue
            hh = str(r.get("appointment_time") or "")[:5]
            motif = r.get("title") or "Rendez-vous"
            rdvs.append((hh or "??:??", motif, r.get("contact_name") or ""))
    except Exception as e:
        print(f"[Notify] digest appointments: {e}")
    rappels.sort()
    rdvs.sort()
    return rappels, rdvs


def format_digest(day_fr: str, rappels: list, rdvs: list) -> tuple:
    subject = f"Votre journée Clarity — {day_fr}"
    lines = [f"Voici ce qui est prévu aujourd'hui ({day_fr}).", ""]
    if rdvs:
        lines.append(f"Rendez-vous ({len(rdvs)})")
        for hh, motif, who in rdvs:
            extra = f" — {who}" if who else ""
            lines.append(f"• {hh}  {motif}{extra}")
        lines.append("")
    if rappels:
        lines.append(f"Rappels ({len(rappels)})")
        for hh, motif, who in rappels:
            extra = f" — {who}" if who and who != "Moi" else ""
            lines.append(f"• {hh}  {motif}{extra}")
        lines.append("")
    if not rdvs and not rappels:
        lines.append("Rien de noté pour aujourd'hui.")
        lines.append("")
    lines.append("— Clarity")
    return subject, "\n".join(lines)


def process_digests(sb: Client, now: datetime, cache: dict, debug: list) -> List[str]:
    sent = []
    today = now.strftime("%Y-%m-%d")
    try:
        profiles = sb.table("profiles").select("*").limit(500).execute().data or []
    except Exception as e:
        debug.append(f"profiles load: {e}")
        return sent
    for prof in profiles:
        uid = str(prof.get("id") or "")
        if not uid:
            continue
        if prof.get("digest_enabled") is False:
            continue
        time_s = str(prof.get("digest_time") or "07:30")[:5]
        target = parse_dt(today, time_s)
        if not target:
            continue
        last = str(prof.get("digest_last_sent") or "")[:10]
        if last == today:
            continue
        # fenêtre : de l'heure choisie jusqu'à +12 min (plusieurs ticks cron)
        if now < target or now - target > timedelta(minutes=12):
            continue
        email = lookup_email(sb, uid, cache) or prof.get("email") or prof.get("user_email")
        if not email:
            debug.append(f"digest no-email {uid}")
            continue
        rappels, rdvs = day_items(sb, uid, today)
        day_fr = now.strftime("%d/%m/%Y")
        subject, body = format_digest(day_fr, rappels, rdvs)
        result = send_email(email, subject, body)
        if result == "ok":
            try:
                sb.table("profiles").update({"digest_last_sent": today}).eq("id", uid).execute()
            except Exception as e:
                print(f"[Notify] digest_last_sent: {e}")
            sent.append(email)
        else:
            debug.append(f"digest send-fail {email}: {result}")
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
    digests = process_digests(sb, now, cache, debug)
    print(f"[Notify] pass {now.isoformat()} rappels={rappels} rdvs={rdvs} debug={debug}")
    return {
        "ok": True,
        "now": now.isoformat(),
        "reminders_sent": rappels,
        "appointments_sent": rdvs,
        "digests_sent": digests,
        "debug": debug[-20:],
    }
