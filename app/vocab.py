"""Mots du jour : 5 nouveaux + 2 à revoir (meilleure utilité)."""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client, Client

load_dotenv()
PARIS = ZoneInfo("Europe/Paris")
MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")


def get_supabase() -> Optional[Client]:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


def get_client() -> OpenAI:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY manquante")
    return OpenAI(api_key=key)


def _today() -> date:
    return datetime.now(PARIS).date()


def list_user_words(user_id: str) -> List[dict]:
    sb = get_supabase()
    if not sb or not user_id:
        return []
    try:
        rows = (
            sb.table("vocab_words")
            .select("*")
            .eq("user_id", user_id)
            .order("sent_on", desc=True)
            .limit(500)
            .execute()
            .data
            or []
        )
        return rows
    except Exception as e:
        print(f"[Vocab] list: {e}")
        return []


def pick_reviews(existing: List[dict], today: date) -> List[dict]:
    cutoff = today - timedelta(days=3)
    scored = []
    for w in existing:
        last = w.get("last_reviewed_on") or w.get("sent_on")
        try:
            last_d = date.fromisoformat(str(last)[:10])
        except Exception:
            last_d = date.min
        if last_d >= cutoff:
            continue
        try:
            score = int(w.get("usefulness") or 0)
        except Exception:
            score = 0
        scored.append((score, last_d, w))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [x[2] for x in scored[:2]]


BANAL = {
    "soutenir", "évoluer", "évolution", "découverte", "découvrir", "pertinent",
    "bienveillance", "important", "importance", "simple", "nouveau", "nouvelle",
    "changer", "aider", "améliorer", "développement", "développer", "projet",
    "équipe", "travailler", "positif", "négatif", "chose", "idée", "besoin",
    "mettre", "faire", "dire", "parler", "penser", "savoir", "pouvoir",
}

NEEDED = 7


def generate_new_words(exclude: List[str], count: int = 5) -> List[dict]:
    banned = ", ".join(list(dict.fromkeys((exclude or []) + list(BANAL)))[:120])
    prompt = f"""Donne EXACTEMENT {count} mots français pour un mail quotidien « mieux s'exprimer ».

NIVEAU OBLIGATOIRE (comme ceci, ni plus plat ni plus pédant) :
temporiser, circonspect, latitude, corroborer, réserve, atermoyer, laconique,
discernement, concéder, sibyllin, préconiser, péremptoire, éluder, hiatus,
parcimonieux, subodorer, mitigé, velléité, tempérer, inéluctable.
Ce sont des EXEMPLES de niveau — n'envoie pas toujours les mêmes.

Interdit : mots d'école primaire (évoluer, soutenir, découverte, pertinent,
bienveillance, important, améliorer, changer, aider, simple…).
Interdit aussi : mots de concours hors oral (amphigouri, lucubration, pérorer).

Alterne verbe / adjectif / nom.
Définition courte. 3 synonymes. Exemple à la 1re personne, oral, entre « ».

Déjà envoyés, ne pas répéter : {banned}

JSON uniquement :
{{
  "words": [
    {{
      "word": "",
      "pos": "verbe|adjectif|nom",
      "definition": "",
      "synonyms": ["", "", ""],
      "example": "",
      "usefulness": 4
    }}
  ]
}}"""
    resp = get_client().chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    data = json.loads(raw)
    out = []
    for item in data.get("words") or []:
        word = re.sub(r"\s+", " ", str(item.get("word") or "")).strip()
        if not word:
            continue
        syn = item.get("synonyms") or []
        if isinstance(syn, list):
            syn_s = ", ".join(str(s).strip() for s in syn if s)[:200]
        else:
            syn_s = str(syn)[:200]
        try:
            use = int(item.get("usefulness") or 3)
        except Exception:
            use = 3
        use = max(1, min(5, use))
        if word.lower() in BANAL:
            continue
        pos = str(item.get("pos") or item.get("register") or "nom").lower()
        if "verb" in pos:
            pos = "verbe"
        elif "adj" in pos:
            pos = "adjectif"
        elif "nom" in pos or "subst" in pos:
            pos = "nom"
        else:
            pos = pos[:20] or "nom"
        out.append(
            {
                "word": word[:80],
                "definition": str(item.get("definition") or "")[:300],
                "synonyms": syn_s,
                "example": str(item.get("example") or "")[:300],
                "register": pos,
                "usefulness": use,
            }
        )
        if len(out) >= count:
            break
    return out


def persist_batch(user_id: str, news: List[dict], reviews: List[dict], today: date) -> List[dict]:
    sb = get_supabase()
    pack = []
    if not sb:
        for w in news:
            pack.append({**w, "kind": "nouveau"})
        for w in reviews:
            pack.append(
                {
                    "word": w.get("word"),
                    "definition": w.get("definition"),
                    "synonyms": w.get("synonyms"),
                    "example": w.get("example"),
                    "register": w.get("register"),
                    "usefulness": w.get("usefulness"),
                    "kind": "a_revoir",
                }
            )
        return pack
    for w in news:
        row = {
            "user_id": user_id,
            "word": w["word"],
            "definition": w.get("definition"),
            "synonyms": w.get("synonyms"),
            "example": w.get("example"),
            "register": w.get("register"),
            "usefulness": w.get("usefulness") or 3,
            "sent_on": today.isoformat(),
            "last_reviewed_on": None,
        }
        try:
            sb.table("vocab_words").insert(row).execute()
        except Exception as e:
            print(f"[Vocab] insert {w.get('word')}: {e}")
        pack.append({**w, "kind": "nouveau"})
    for w in reviews:
        try:
            sb.table("vocab_words").update(
                {"last_reviewed_on": today.isoformat()}
            ).eq("id", w["id"]).execute()
        except Exception as e:
            print(f"[Vocab] review {w.get('word')}: {e}")
        pack.append(
            {
                "word": w.get("word"),
                "definition": w.get("definition"),
                "synonyms": w.get("synonyms"),
                "example": w.get("example"),
                "register": w.get("register"),
                "usefulness": w.get("usefulness"),
                "kind": "a_revoir",
            }
        )
    return pack


def build_daily_pack(user_id: str) -> List[dict]:
    today = _today()
    existing = list_user_words(user_id)
    already_today = [w for w in existing if str(w.get("sent_on") or "")[:10] == today.isoformat()]
    reviewed_today = [
        w
        for w in existing
        if str(w.get("last_reviewed_on") or "")[:10] == today.isoformat()
    ]
    if len(already_today) >= 5:
        pack = [{**w, "kind": "nouveau"} for w in already_today[:5]]
        pack += [{**w, "kind": "a_revoir"} for w in reviewed_today[:2]]
        return pack
    reviews = pick_reviews(existing, today)
    exclude = [str(w.get("word") or "") for w in existing]
    need_new = NEEDED - len(reviews)
    try:
        news = generate_new_words(exclude, count=need_new)
    except Exception as e:
        print(f"[Vocab] generate: {e}")
        news = []
    return persist_batch(user_id, news, reviews, today)


def format_vocab_block(words: List[dict], html: bool = False) -> str:
    if not words:
        return ""
    n = len(words)
    title = f"{n} mots pour mieux s'exprimer"
    if html:
        parts = [f"<p><strong>{title}</strong></p><ol>"]
        for i, w in enumerate(words, 1):
            name = str(w.get("word") or "")
            pos = w.get("register") or ""
            review = " — à revoir" if w.get("kind") == "a_revoir" else ""
            label = f"{name}" + (f" ({pos})" if pos else "") + review
            block = f"<li><strong>{label}</strong>"
            if w.get("definition"):
                block += f"<br>{w['definition']}"
            if w.get("synonyms"):
                block += f"<br>Synonymes : {w['synonyms']}"
            if w.get("example"):
                ex = str(w["example"]).strip()
                if not ex.startswith("«"):
                    ex = f"« {ex} »"
                block += f"<br>{ex}"
            block += "</li>"
            parts.append(block)
        parts.append("</ol>")
        return "\n".join(parts)
    lines = [title, ""]
    for i, w in enumerate(words, 1):
        pos = f" ({w.get('register')})" if w.get("register") else ""
        tag = " — à revoir" if w.get("kind") == "a_revoir" else ""
        lines.append(f"{i}. {w.get('word')}{pos}{tag}")
        if w.get("definition"):
            lines.append(f"   {w['definition']}")
        if w.get("synonyms"):
            lines.append(f"   Synonymes : {w['synonyms']}")
        if w.get("example"):
            ex = str(w["example"]).strip()
            if not ex.startswith("«"):
                ex = f"« {ex} »"
            lines.append(f"   {ex}")
        lines.append("")
    return "\n".join(lines)


def history_payload(user_id: str) -> dict:
    rows = list_user_words(user_id)
    items = []
    for w in rows:
        items.append(
            {
                "id": w.get("id"),
                "word": w.get("word"),
                "definition": w.get("definition"),
                "synonyms": w.get("synonyms"),
                "example": w.get("example"),
                "register": w.get("register"),
                "usefulness": w.get("usefulness"),
                "sent_on": str(w.get("sent_on") or "")[:10],
                "last_reviewed_on": str(w.get("last_reviewed_on") or "")[:10] or None,
            }
        )
    return {"success": True, "words": items}
