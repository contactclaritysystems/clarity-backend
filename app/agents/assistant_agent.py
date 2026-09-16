"""
Agent Assistant Clarity — natif Render
Répond avec le contexte réel de l'utilisateur (planning, relances, profil).
"""

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Any, Dict, List
from openai import OpenAI
from dotenv import load_dotenv
from app.memory import load_memory, memory_as_text
from supabase import create_client, Client

load_dotenv()
MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")


def get_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY manquante")
    return OpenAI(api_key=api_key)


JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = [
    "", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def format_fr_humain(date_s: str, time_s: str = "") -> str:
    try:
        d = datetime.strptime(str(date_s)[:10], "%Y-%m-%d")
        t = str(time_s or "")[:5]
        heure = ""
        if t and t[0].isdigit():
            h, m = t.split(":")[:2]
            heure = f" à {int(h)}h" + (f"{m}" if m != "00" else "")
        return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month]} {d.year}{heure}"
    except Exception:
        return f"{date_s} {time_s}".strip()


def get_supabase() -> Optional[Client]:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


def load_user_context(user_id: Optional[str], user_name: str = "") -> str:
    """Charge un résumé court et utile (pas tout l'historique)."""
    if not user_id:
        return f"Utilisateur : {user_name or 'inconnu'} (pas d'user_id → pas de données planning)."

    sb = get_supabase()
    if not sb:
        return f"Utilisateur : {user_name or user_id}. Supabase indisponible."

    lines: List[str] = [f"Utilisateur : {user_name or user_id}"]
    now = datetime.now(ZoneInfo("Europe/Paris"))
    today = now.strftime("%Y-%m-%d")
    in_14 = (now + timedelta(days=14)).strftime("%Y-%m-%d")

    # RDV à venir
    try:
        appts = (
            sb.table("appointments")
            .select("title, appointment_date, appointment_time, contact_name, status, description")
            .eq("user_id", user_id)
            .gte("appointment_date", today)
            .lte("appointment_date", in_14)
            .order("appointment_date")
            .limit(15)
            .execute()
        )
        rows = appts.data or []
        if rows:
            lines.append("Rendez-vous (14 prochains jours) :")
            for a in rows:
                d = format_fr_humain(
                    a.get("appointment_date") or "",
                    a.get("appointment_time") or "",
                )
                title = a.get("title") or "RDV"
                contact = a.get("contact_name") or ""
                st = (a.get("status") or "").lower()
                status_fr = {
                    "done": "terminé",
                    "completed": "terminé",
                    "scheduled": "à venir",
                    "pending": "à venir",
                    "cancelled": "annulé",
                    "canceled": "annulé",
                }.get(st, "")
                bit = f"- {d}"
                bit += f" — {title}"
                if contact:
                    bit += f" avec {contact}"
                if status_fr:
                    bit += f" ({status_fr})"
                lines.append(bit)
        else:
            lines.append("Rendez-vous (14 j) : aucun.")
    except Exception as e:
        lines.append(f"Rendez-vous : erreur lecture ({e})")

    # Relances / rappels pending
    try:
        fus = (
            sb.table("follow_ups")
            .select("reason, reminder_date, reminder_time, contact_name, status, message_context")
            .eq("user_id", user_id)
            .eq("status", "pending")
            .order("reminder_date")
            .limit(15)
            .execute()
        )
        rows = fus.data or []
        if rows:
            lines.append("Rappels / relances en attente :")
            for f in rows:
                d = f.get("reminder_date") or "?"
                if len(str(d)) >= 10:
                    y, m, day = str(d)[:10].split("-")
                    d = f"{day}/{m}/{y}"
                t = f.get("reminder_time") or ""
                t = str(t)[:5]
                reason = f.get("reason") or "Rappel"
                contact = f.get("contact_name") or ""
                bit = f"- {d}"
                if t:
                    bit += f" {t}"
                bit += f" — {reason}"
                if contact and contact != "Moi":
                    bit += f" ({contact})"
                lines.append(bit)
        else:
            lines.append("Rappels / relances en attente : aucun.")
    except Exception as e:
        lines.append(f"Rappels : erreur lecture ({e})")

    # Quelques contacts (pour questions du type "j'ai le contact de…")
    try:
        contacts = (
            sb.table("contacts")
            .select("full_name, email, company")
            .eq("user_id", user_id)
            .order("full_name")
            .limit(30)
            .execute()
        )
        rows = contacts.data or []
        if rows:
            lines.append(f"Contacts enregistrés ({len(rows)} affichés, max 30) :")
            for c in rows[:20]:
                name = c.get("full_name") or "?"
                email = c.get("email") or ""
                company = c.get("company") or ""
                bit = f"- {name}"
                if company:
                    bit += f" ({company})"
                if email:
                    bit += f" — {email}"
                lines.append(bit)
        else:
            lines.append("Contacts : aucun.")
    except Exception as e:
        lines.append(f"Contacts : erreur lecture ({e})")

    return "\n".join(lines)


SYSTEM = """Tu es Clarity, assistante professionnelle. VOUVOIEMENT uniquement.

DATE : la ligne "Date/heure actuelle" est la vérité (Europe/Paris). "Aujourd'hui" = cette date-là.

RÈGLES :
1. Planning / rappels / contacts : uniquement le CONTEXTE CLARITY. Pas d'invention.
2. Question d'actualité, société, sport, prix : priorisez RECHERCHE WEB.
   INTERDIT de dire que vos connaissances s'arrêtent en 2023 ou qu'vous n'avez pas Internet.
   Si le web est vide : deux phrases max, sans « dernière mise à jour », sans année de coupure, sans inventer l'actu.
3. Jamais de markdown (**gras**, puces *). Texte simple, listes avec des tirets.
4. Jamais les codes done / scheduled / pending. Pas "(à venir)" si c'est déjà dit par la date.
   Dates TOUJOURS en français comme dans le contexte (mercredi 16 septembre 2026 à 15h).
   INTERDIT : 16/09/2026, 2026-09-16, 15:00 seul.
5. Pas de phrase de fin commerciale ("n'hésitez pas", "je reste à votre disposition").
6. Devis *à créer*, WhatsApp, agenda Google : dites en 2 phrases que c'est bientôt.
6b. DOCUMENT JOINT : uniquement LE fichier de cette requête.
   Répondez d'abord à ce qui est VISIBLE (photo/PDF).
   INTERDIT d'utiliser le carnet de contacts pour « qui est sur la photo ».
   Vous ne reconnaissez pas une personne par son visage.
   Photo + « qui sont-ils » : d'abord décrire le visible
   (nombre, âge apparent, attitude). Ensuite UNE phrase :
   pas de prénom sauf si l'utilisateur l'a dit.
   INTERDIT de répondre seulement « je ne peux pas identifier ».
   Structure documents texte : type, résumé, points importants.
   Pas de rubrique « zones illisibles » si tout est lisible.
7. DROIT / FISCALITÉ / TRAVAIL / OBLIGATIONS LÉGALES :
   - Réponse générale et prudente uniquement. Jamais « la loi impose X » comme un verdict.
   - Terminez TOUJOURS par exactement :
     « Ceci n'est pas un conseil juridique. Vérifiez auprès d'un professionnel ou sur un site officiel (ex. service-public.fr). »
   - Pas d'article de loi inventé, pas de montant de cotisation/amende inventé.
"""





def is_legal_sensitive(instruction: str) -> bool:
    t = (instruction or "").lower()
    keys = [
        "obligation légale", "obligations légales", "fiscal", "fiscalité", "urssaf",
        "impôt", "impots", "tva", "droit du travail", "code du travail",
        "licenciement", "contrat de travail", "affichage des prix", "amende",
        "conformité", "rgpd", "legal", "juridique", "avocat", "expert-comptable",
        "cotisation", "charges sociales", "smic", "congés payés", "préavis",
    ]
    return any(k in t for k in keys)


LEGAL_DISCLAIMER = (
    "Ceci n'est pas un conseil juridique. Vérifiez auprès d'un professionnel "
    "ou sur un site officiel (ex. service-public.fr)."
)

def needs_web_search(instruction: str) -> bool:
    """Questions d'actu / faits externes / culture générale hors Clarity."""
    t = (instruction or "").lower()
    perso = [
        "rendez-vous", "rdv", "rappel", "relance", "mon contact", "mes contacts",
        "mon planning", "ma journée", "mes rendez-vous", "mes rappels",
        "fais-moi penser", "ajoute un", "crée un",
    ]
    # purement perso Clarity
    if any(p in t for p in perso) and not any(
        k in t for k in ("bitcoin", "bourse", "actualité", "prix", "cours")
    ):
        return False
    return True  # par défaut on tente le web pour l'assistant "question"


def _http_json(url: str, timeout: int = 10):
    import json
    import urllib.request
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ClarityAssistant/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def _http_text(url: str, timeout: int = 10) -> str:
    import urllib.request
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; ClarityBot/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def search_crypto(instruction: str) -> str:
    """Prix crypto via CoinGecko (gratuit, fiable)."""
    t = (instruction or "").lower()
    mapping = {
        "bitcoin": "bitcoin",
        "btc": "bitcoin",
        "ethereum": "ethereum",
        "eth": "ethereum",
        "solana": "solana",
        "sol": "solana",
        "dogecoin": "dogecoin",
        "doge": "dogecoin",
        "cardano": "cardano",
        "ada": "cardano",
        "xrp": "ripple",
        "ripple": "ripple",
    }
    coin_id = None
    for k, cid in mapping.items():
        if k in t:
            coin_id = cid
            break
    if not coin_id and "crypto" not in t:
        return ""
    if not coin_id:
        coin_id = "bitcoin"
    try:
        url = (
            f"https://api.coingecko.com/api/v3/simple/price"
            f"?ids={coin_id}&vs_currencies=eur,usd&include_24hr_change=true"
        )
        data = _http_json(url)
        info = data.get(coin_id) or {}
        if not info:
            return ""
        eur = info.get("eur")
        usd = info.get("usd")
        ch = info.get("eur_24h_change") or info.get("usd_24h_change")
        lines = [f"Prix {coin_id} (CoinGecko, temps réel) :"]
        if eur is not None:
            lines.append(f"- {eur:,.2f} EUR".replace(",", " "))
        if usd is not None:
            lines.append(f"- {usd:,.2f} USD".replace(",", " "))
        if ch is not None:
            lines.append(f"- Variation 24h : {ch:+.2f} %")
        return "\n".join(lines)
    except Exception as e:
        print(f"[crypto] {e}")
        return ""



def search_wiki_summary(query: str) -> str:
    try:
        import urllib.parse, json
        title = urllib.parse.quote(query.strip().replace(" ", "_"))
        for lang in ("fr", "en"):
            url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
            try:
                data = _http_json(url)
            except Exception:
                continue
            extract = (data.get("extract") or "").strip()
            label = data.get("title") or query
            if extract:
                return f"{label} : {extract[:800]}"
    except Exception as e:
        print(f"[wiki-sum] {e}")
    return ""


def search_wikipedia(query: str) -> str:
    try:
        import urllib.parse
        q = urllib.parse.quote(query)
        url = (
            "https://fr.wikipedia.org/w/api.php?action=query&list=search"
            f"&srsearch={q}&utf8=&format=json&srlimit=3"
        )
        data = _http_json(url)
        hits = ((data.get("query") or {}).get("search")) or []
        if not hits:
            # fallback EN
            url = (
                "https://en.wikipedia.org/w/api.php?action=query&list=search"
                f"&srsearch={q}&utf8=&format=json&srlimit=3"
            )
            data = _http_json(url)
            hits = ((data.get("query") or {}).get("search")) or []
        parts = []
        for h in hits[:3]:
            title = h.get("title") or ""
            snippet = re.sub(r"<[^>]+>", "", h.get("snippet") or "")
            if title or snippet:
                parts.append(f"- {title} : {snippet}")
        return "\n".join(parts)
    except Exception as e:
        print(f"[wikipedia] {e}")
        return ""


def search_duckduckgo(query: str) -> str:
    try:
        import json
        import urllib.parse
        import urllib.request
        import re as _re
        q = urllib.parse.quote(query)
        # Instant Answer API
        url = f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1"
        data = _http_json(url)
        parts = []
        if data.get("AbstractText"):
            parts.append(data["AbstractText"])
        for t in (data.get("RelatedTopics") or [])[:5]:
            if isinstance(t, dict) and t.get("Text"):
                parts.append(t["Text"])
        if parts:
            return "\n".join(f"- {p}" for p in parts[:5])
        # HTML fallback
        html = _http_text(f"https://html.duckduckgo.com/html/?q={q}")
        snippets = _re.findall(r'class="result__snippet"[^>]*>(.*?)</', html, _re.I | _re.S)
        out = []
        for s in snippets[:5]:
            clean = _re.sub(r"<[^>]+>", "", s).strip()
            if clean:
                out.append(f"- {clean}")
        return "\n".join(out)
    except Exception as e:
        print(f"[ddg] {e}")
        return ""


def web_search(query: str, max_results: int = 5) -> str:
    """Agrège plusieurs sources gratuites."""
    blocks = []
    crypto = search_crypto(query)
    if crypto:
        blocks.append(crypto)
    wsum = search_wiki_summary(query)
    if wsum:
        blocks.append("Wikipedia :\n" + wsum)
    wiki = search_wikipedia(query)
    if wiki and not wsum:
        blocks.append("Wikipedia :\n" + wiki)
    ddg = search_duckduckgo(query)
    if ddg:
        blocks.append("Web :\n" + ddg)
    return "\n\n".join(blocks) if blocks else ""


def _decode_b64(raw: str) -> bytes:
    import base64
    s = (raw or "").strip()
    if "," in s and s.strip().startswith("data:"):
        s = s.split(",", 1)[1]
    return base64.b64decode(s)


def extract_pdf_bytes(data: bytes) -> str:
    try:
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for i, page in enumerate(reader.pages[:20]):
            pages.append(page.extract_text() or "")
        return "\n".join(pages).strip()
    except Exception as e:
        print(f"[Assistant] pdf: {e}")
        return ""


def pdf_pages_as_jpeg_b64(data: bytes, max_pages: int = 3):
    """PDF scanné : rend les 1ères pages en JPEG. Retourne (liste, erreur)."""
    errors = []
    try:
        import io
        import base64
        import fitz
        doc = fitz.open(stream=data, filetype="pdf")
        out = []
        for i in range(min(max_pages, doc.page_count)):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
            out.append(base64.b64encode(pix.tobytes("jpeg")).decode("ascii"))
        doc.close()
        if out:
            return out, ""
        errors.append("fitz: 0 page")
    except Exception as e:
        errors.append(f"fitz: {e}")
        print(f"[Assistant] pdf-render fitz: {e}")
    try:
        import io
        import base64
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(data)
        out = []
        n = min(max_pages, len(doc))
        for i in range(n):
            page = doc[i]
            bitmap = page.render(scale=1.4)
            pil = bitmap.to_pil()
            buf = io.BytesIO()
            pil.convert("RGB").save(buf, format="JPEG", quality=75)
            out.append(base64.b64encode(buf.getvalue()).decode("ascii"))
        if out:
            return out, ""
        errors.append("pdfium: 0 page")
    except Exception as e:
        errors.append(f"pdfium: {e}")
        print(f"[Assistant] pdf-render pdfium: {e}")
    return [], " | ".join(errors)


def heic_to_jpeg_b64(raw: bytes) -> str:
    try:
        import io
        import base64
        from pillow_heif import register_heif_opener
        from PIL import Image
        register_heif_opener()
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        print(f"[Assistant] heic: {e}")
        return ""


def describe_image_b64(b64: str, mime: str, question: str) -> str:
    try:
        low = (mime or "").lower()
        if "heic" in low or "heif" in low:
            raw = _decode_b64(b64)
            conv = heic_to_jpeg_b64(raw)
            if conv:
                b64, mime = conv, "image/jpeg"
        url = b64 if str(b64).startswith("data:") else f"data:{mime or 'image/jpeg'};base64,{b64}"
        r = get_client().chat.completions.create(
            model=os.getenv("VISION_MODEL", "gpt-4o"),
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        "Décris UNIQUEMENT cette image. "
                        "Compte les personnes une par une (enfant ou adulte). "
                        "N'invente personne en plus. "
                        f"Question : {question or 'décris'}"
                    )},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            }],
            max_tokens=1200,
            temperature=0.1,
        )
        return (r.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"[Assistant] vision: {e}")
        return ""


def load_documents_block(payload: dict) -> str:
    prev = (payload.get("document_extract") or "").strip()
    atts = payload.get("attachments") or payload.get("files") or []
    has_new = isinstance(atts, list) and any(
        isinstance(a, dict) and (a.get("content_b64") or a.get("content")) for a in atts
    )
    if not has_new:
        return prev[:12000]
    if not isinstance(atts, list) or not atts:
        return prev[:12000]
    chunks = []
    for att in atts[:5]:
        if not isinstance(att, dict):
            continue
        name = att.get("filename") or att.get("name") or "fichier"
        mime = (att.get("mime") or att.get("type") or "").lower()
        b64 = att.get("content_b64") or att.get("content") or ""
        if not b64:
            continue
        try:
            raw = _decode_b64(b64)
        except Exception:
            chunks.append(f"[{name}] fichier illisible")
            continue
        if "pdf" in mime or name.lower().endswith(".pdf"):
            txt = extract_pdf_bytes(raw)
            if len(txt) >= 80:
                chunks.append(f"=== PDF {name} ===\n{txt}")
            else:
                pages, perr = pdf_pages_as_jpeg_b64(raw, 3)
                if not pages:
                    chunks.append(
                        f"=== PDF {name} ===\n"
                        "Lecture image du PDF impossible. "
                        f"Détail technique : {perr or 'inconnu'}. "
                        "Joindre une photo JPG d'une page fonctionne toujours."
                    )
                else:
                    bits = []
                    for i, jb64 in enumerate(pages, 1):
                        bits.append(
                            describe_image_b64(
                                jb64,
                                "image/jpeg",
                                payload.get("instruction") or "Lis tout le texte visible",
                            )
                            or f"(page {i} non lue)"
                        )
                    chunks.append(f"=== PDF {name} (lu comme image) ===\n" + "\n\n".join(bits))
        elif mime.startswith("image/") or name.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".heic")):
            desc = describe_image_b64(b64, mime or "image/jpeg", payload.get("instruction") or "")
            chunks.append(f"=== PHOTO {name} ===\n{desc or '(image non lue)'}")
        else:
            chunks.append(f"[{name}] type non lu en V1 ({mime})")
    nouveau = "\n\n".join(chunks).strip()
    if prev and nouveau:
        return (prev + "\n\n=== FICHIER AJOUTÉ ===\n" + nouveau)[:12000]
    return (nouveau or prev)[:12000]


async def run_assistant_agent(payload: dict) -> dict:
    instruction = (payload.get("instruction") or "").strip()
    request_id = payload.get("request_id")
    user_id = payload.get("user_id")
    user_name = (payload.get("user_name") or "").strip()
    history = payload.get("conversation_history") or ""
    docs = load_documents_block(payload)
    if not instruction and docs:
        instruction = "Analysez ce document."

    if not instruction:
        return {
            "success": False,
            "message": "Quelle est votre question ?",
            "request_id": request_id,
        }

    try:
        if docs:
            context = f"Utilisateur : {user_name or 'client'}."
        else:
            context = load_user_context(user_id, user_name)
            mem = memory_as_text(load_memory(user_id))
            if mem:
                context = context + "\n\n" + mem
        today_fr = datetime.now(ZoneInfo("Europe/Paris")).strftime("%d/%m/%Y %H:%M")

        web_block = ""
        if docs:
            web_block = ""
        elif needs_web_search(instruction):
            raw = web_search(instruction)
            if raw:
                web_block = f"=== RECHERCHE WEB (faits récupérés maintenant) ===\n{raw}\n\n"
            else:
                web_block = (
                    "=== RECHERCHE WEB ===\n"
                    "Aucun extrait live récupéré. Répondez quand même avec vos "
                    "connaissances générales, en précisant clairement si l'info "
                    "peut avoir changé (surtout cours, actu, sport).\n\n"
                )

        user_msg = (
            f"Date/heure actuelle : {today_fr}\n\n"
            f"=== CONTEXTE CLARITY (données réelles) ===\n{context}\n\n"
        )
        if docs:
            user_msg += (
                "=== DOCUMENT FOURNI (source unique pour cette question) ===\n"
                f"{docs}\n\n"
            )
        if web_block:
            user_msg += web_block
        if history:
            user_msg += f"=== HISTORIQUE RÉCENT ===\n{history}\n\n"
        user_msg += f"=== QUESTION ===\n{instruction}"

        response = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=900,
        )
        answer = (response.choices[0].message.content or "").strip()
        low_i = instruction.lower()
        coming = (not docs) and any(k in low_i for k in (
            "whatsapp", "chantier", "google calendar",
            "agenda google",
        ))
        if not answer:
            answer = "Je n'ai pas pu formuler de réponse. Reformulez votre question."

        import re
        answer = re.sub(r"\*\*", "", answer)
        answer = re.sub(
            r"\n*Zones? illisibles?\s*:\s*(Aucune|aucune|Pas |pas |N['’]aucune)[^\n]*",
            "",
            answer,
        )
        if is_legal_sensitive(instruction) or is_legal_sensitive(docs):
            if LEGAL_DISCLAIMER.lower() not in answer.lower():
                answer = (answer.rstrip() + "\n\n" + LEGAL_DISCLAIMER).strip()
        return {
            "success": True,
            "title": "Clarity",
            "message": answer,
            "content": answer,
            "coming_soon": bool(coming),
            "document_extract": docs[:8000] if docs else None,
            "request_id": request_id,
        }
    except Exception as e:
        print(f"[Assistant] error: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "message": f"Erreur assistant : {e}",
            "request_id": request_id,
        }
