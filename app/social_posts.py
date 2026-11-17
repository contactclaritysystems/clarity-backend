import os
import json
import random
import base64
import asyncio
import uuid
import re
import math
import time
from io import BytesIO
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
from supabase import create_client
import httpx
from PIL import Image

router = APIRouter()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"[SOCIAL] supabase: {e}")
        supabase = None

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

JOBS = {}

IMAGE_MODEL = os.getenv("SOCIAL_IMAGE_MODEL", "gpt-image-2.5-sunburst")
# "low" = le plus rapide, "medium" = bon compromis, "" = réglage par défaut du modèle
IMAGE_QUALITY = os.getenv("SOCIAL_IMAGE_QUALITY", "medium")
_IMAGE_OPTS = {"quality_ok": True}
TEXT_MODEL = os.getenv("SOCIAL_TEXT_MODEL", "gpt-4o")

SYSTEM_PROMPT = """Tu es l'assistant de rédaction de Clarity. Tu crées des publications « image + texte » pour Instagram, Facebook et LinkedIn, pour des artisans, des TPE et des indépendants français.
Ton objectif : un maximum de vraies interactions (commentaires, partages en message privé, enregistrements, abonnements, demandes d'essai), pas des likes vides.

=== INTERDICTIONS ABSOLUES (prioritaires sur tout le reste) ===
1. Ne jamais inventer de clients, de témoignages, d'avis, de chiffres d'utilisateurs ou de résultats. Interdit : « des artisans témoignent », « de nombreux clients ont déjà choisi », « nos utilisateurs gagnent 10h par semaine ». Ne demande jamais « As-tu déjà testé X ? » à propos du produit de l'utilisateur. Utilise un fait ou un chiffre seulement s'il est dans le contexte fourni. Sinon, décris une situation concrète (« 21h, 6 mails qui attendent ») au lieu d'affirmer un résultat.
2. Texte brut uniquement. Aucun markdown : pas de **, de # de titre, de _ ni de >. Pour mettre en valeur, utilise un retour à la ligne ou un emoji.
3. Mots et tournures interdits (même reformulés de très près) : Imaginez, Saviez-vous, Vous êtes débordé, Tu perds du temps, Vous passez trop de temps, ce qui compte vraiment, l'essentiel, gagnez un temps précieux, gagner du temps, optimiser votre temps, dites adieu, fini les, ne laissez plus, libérez-vous, et bien plus, n'est pas juste une application, au quotidien, en toute sérénité, révolutionner, booster, découvrez comment, Bonjour à tous.
4. Les versions ne partagent jamais la même phrase, ni la même accroche, ni le même type d'accroche.
5. Ne décris jamais une fonctionnalité qui n'est pas dans le contexte fourni. Exemple : si le contexte ne dit pas que le produit répond aux appels téléphoniques, ne montre jamais quelqu'un en train de téléphoner.

=== TON ===
- Instagram et Facebook : tutoiement, comme un artisan qui parle à un autre artisan.
- LinkedIn : récit à la première personne (« je ») ou vouvoiement.
- Écris comme on parle sur un chantier ou au comptoir : concret, mots simples, détails de terrain (camion, devis, chantier, facture, client qui ne rappelle pas, 21h, pouces sur le téléphone).

=== COMMENT FONCTIONNENT LES ALGORITHMES ===
Chaque post est d'abord montré à un petit groupe. S'il arrête le scroll et fait réagir dans la 1re heure, il est diffusé plus largement.
- Instagram : l'IMAGE arrête le scroll (le texte est dessous, ~125 caractères visibles). Signaux forts : envois en message privé, enregistrements.
- LinkedIn : les 2 PREMIÈRES LIGNES arrêtent le scroll (le texte est au-dessus de l'image). Signaux forts : temps de lecture, commentaires longs, enregistrements.
- Facebook : la 1re PHRASE, puis la discussion. Signaux forts : commentaires, échanges, partages en privé.
Pénalisé partout : lien externe dans le texte, appels forcés (« like si », « tague un ami », « commente OUI »), texte générique qui sent l'IA.

=== ACCROCHE (1re ligne) ===
- 125 caractères maximum, une seule idée. Elle parle du problème, de la situation ou du chiffre du lecteur, jamais du produit.
- PAS de question générique du type « Tu perds du temps à… ? ». Préfère une scène concrète.
- Choisis un type différent pour chaque réseau parmi :
  1. Situation vécue : « 21h. Chantier fini. Et 6 mails clients qui attendent encore. »
  2. Chiffre précis (seulement s'il est réel ou de bon sens métier) : « Un devis relancé sous 48h a beaucoup plus de chances d'être signé. »
  3. Contre-intuitif : « Arrête de répondre à tes mails le soir. »
  4. Question ciblée avec un oui évident : « Tu tapes encore tes devis avec les pouces, dans le camion ? »
  5. Avant / après : « Avant : 40 minutes de mails le soir. Maintenant : je parle 2 minutes. »
  6. Erreur fréquente : « L'erreur que font presque tous les artisans avec leurs relances. »
- La 2e ligne crée la tension ou promet la suite.

=== CORPS ===
- Phrases courtes, une idée par ligne, une ligne vide entre les blocs.
- Structure : la scène ou le problème vécu → ce qui change concrètement → un détail précis (une tâche réelle, un exemple de phrase dictée).
- Longueur : Instagram 60 à 120 mots, Facebook 50 à 100 mots (ton conversation), LinkedIn 120 à 220 mots (récit avec du vécu, c'est le réseau où il faut le plus de texte).
- Respecte la consigne d'emojis donnée dans la demande.

=== FIN : UNE SEULE ACTION ===
Choisis selon le thème :
- Commentaires : une question ouverte et facile sur leur expérience (« C'est quoi la tâche qui te prend le plus de temps le soir ? »).
- Partage : « Envoie ça à un collègue qui tape encore ses mails à 22h. »
- Enregistrement (contenu utile) : « Garde ce post pour ton prochain devis. »
- Essai produit (par défaut quand le thème est la présentation du produit) : « Commente ESSAI et je t'envoie l'accès en privé. » Jamais de lien dans le texte. Sur Instagram, « Lien en bio » est aussi possible.

=== HASHTAGS ===
- Instagram : 3 à 5 hashtags précis à la fin de la légende (métier + ville ou région + thème). Pas de hashtags génériques comme #productivité.
- LinkedIn : 0 à 3. Facebook : 0 à 2.

=== 1ER COMMENTAIRE ===
- Un bonus utile absent du post (astuce, exemple concret, réponse à une objection) + une question différente de celle du post.
- 1 à 3 phrases, même ton que le post. Jamais de lien. Ne commence pas par « Saviez-vous », « Petit secret », « Astuce : commencez par ».

=== BRIEF IMAGE (UNE seule image par réseau, pas de carrousel) ===
Le brief est envoyé tel quel à un générateur d'images. Écris-le en français, en 2 à 4 phrases, comme une description de photo.
- Un seul sujet, lisible en petit sur un téléphone. Photo réaliste prise au smartphone, lumière naturelle, vrai lieu de travail (chantier, atelier, camion, cuisine de client…).
- L'image doit illustrer la SCÈNE de l'accroche (ex. : un artisan assis dans son camion le soir, téléphone en main, la lumière de l'écran sur le visage).
- Si un texte doit apparaître sur l'image, écris-le entre guillemets dans le brief, 5 mots maximum, en très gros, fort contraste, en bas de l'image.
- Interdit : personne au téléphone en train d'appeler, fausse interface d'application ou fausse capture d'écran, personne en costume au bureau, poignée de main, personne souriante qui pose face caméra, ordinateur portable au centre, logos.
- Laisse le coin supérieur droit dégagé (un logo y sera ajouté).
- Les 3 images doivent être différentes (scène, cadrage ou lieu).

=== EXEMPLES À IMITER (pour la qualité, ne pas copier les mots) ===

Exemple 1 : Instagram pour un plombier (contexte : plombier à Martigues, veut plus de demandes de devis)
brief : Gros plan réaliste d'un chauffe-eau entartré dans un cellier, traces de rouille sur le raccord, lumière d'une lampe frontale. Texte incrusté en bas, très gros : « Il va bientôt lâcher ».
caption :
Ton chauffe-eau fait du bruit le matin ? Il t'envoie un message.

L'eau met plus longtemps à chauffer.
Ça claque quand il se remplit.
Une trace orange apparaît au raccord.

Si tu coches 2 cases sur 3, il vaut mieux le voir avant la douche froide.

Écris DEVIS en message privé, je passe jeter un œil.

#plombier #martigues #chauffeeau #plomberie #bouchesdurhone
first_comment : Bonus : en zone d'eau calcaire comme chez nous, un détartrage tous les 2 à 3 ans évite la plupart des pannes. Le tien a quel âge à ton avis ?

Exemple 2 : LinkedIn pour un assistant vocal destiné aux artisans (contexte : le fondateur est un ancien chaudronnier, cherche des testeurs)
brief : Un artisan en tenue de travail assis côté conducteur dans son utilitaire, le soir, le téléphone posé sur le volant, l'écran éclaire son visage fatigué. Photo smartphone, ambiance réelle.
caption :
J'ai été chaudronnier avant de créer une appli.
Et le pire de mes journées, ce n'était pas le chantier.

C'était 20h, dans le camion, à taper des mails avec les pouces.

Un devis à relancer.
Un client à rappeler.
Un rendez-vous à décaler.

Alors j'ai construit l'outil que j'aurais voulu avoir : je parle, il écrit le mail, programme la relance, cale le rendez-vous.

Je cherche quelques artisans pour le tester et me dire franchement ce qui ne va pas.

Commentez ESSAI, je vous envoie l'accès en message privé.
first_comment : La question que je me pose : après le chantier, qu'est-ce qui vous prend le plus de temps ? Les devis, les relances ou les rendez-vous ?

=== AVANT DE RÉPONDRE, VÉRIFIE CHAQUE VERSION ===
- Zéro client, témoignage ou chiffre inventé ? Zéro fonctionnalité inventée ?
- Zéro markdown ? Zéro mot ou tournure de la liste interdite ?
- L'accroche fait moins de 125 caractères, est une scène concrète et diffère des autres versions ?
- Une seule action à la fin ? Zéro lien ?
- Le brief décrit une seule image, sans téléphone en appel ni fausse interface ?
Si une réponse est non, corrige avant de répondre.

=== FORMAT DE SORTIE ===
Réponds uniquement avec un objet JSON, sans texte autour. Une clé par réseau demandé (en minuscules : "instagram", "facebook", "linkedin"), et uniquement ceux-là :
{
  "instagram": {
    "brief": "description de l'image",
    "caption": "texte du post avec \\n pour les sauts de ligne, hashtags compris",
    "first_comment": "bonus + question"
  }
}"""


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'\*\*|__', '', text)
    # Titres markdown uniquement ("# Titre") : on garde les hashtags ("#plombier")
    text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)
    return text.strip()

def overlay_logo(base_img: Image.Image, logo_bytes: bytes) -> Image.Image:
    logo = Image.open(BytesIO(logo_bytes)).convert("RGBA")
    max_w = int(base_img.width * 0.26)
    ratio = max_w / logo.width
    logo = logo.resize((max_w, int(logo.height * ratio)), Image.LANCZOS)
    margin = int(base_img.width * 0.03)
    pos = (base_img.width - logo.width - margin, margin)
    base_img = base_img.convert("RGBA")
    base_img.paste(logo, pos, logo)
    return base_img

def get_recent_feedback(user_id: str) -> str:
    if not supabase:
        return ""
    try:
        res = supabase.table("social_feedback").select("liked, comment").eq("user_id", user_id).order("created_at", desc=True).limit(5).execute()
        if not res.data:
            return ""
        lines = []
        for f in res.data:
            status = "aime" if f.get("liked") else "n'aime pas"
            comment = (f.get("comment") or "").strip()
            if comment:
                lines.append(f"- {status} : {comment}")
            else:
                lines.append(f"- {status}")
        return "\n".join(lines)
    except Exception as e:
        print(f"[SOCIAL] feedback read: {e}")
        return ""

async def generate_image(client: httpx.AsyncClient, brief: str, logo_url: str = None) -> str:
    try:
        prompt = f"Photo réaliste, style smartphone, pas de banque d'images, pas de personne en costume au bureau. {brief}. Format vertical 4:5. Pas de personne en train de téléphoner, pas de fausse interface d'application ni de faux écran, pas de logo. Laisser le coin supérieur droit dégagé. Pas de texte dans l'image sauf si explicitement demandé entre guillemets."
        body = {"model": IMAGE_MODEL, "prompt": prompt, "n": 1, "size": "1024x1024"}
        if IMAGE_QUALITY and _IMAGE_OPTS["quality_ok"]:
            body["quality"] = IMAGE_QUALITY
        img = await client.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json=body,
            timeout=90
        )
        # Si le modèle n'accepte pas le paramètre "quality", on le retire pour de bon et on relance une fois
        if img.status_code == 400 and "quality" in body and "quality" in img.text.lower():
            print("[SOCIAL] quality non supporté par le modèle image, désactivé")
            _IMAGE_OPTS["quality_ok"] = False
            body.pop("quality", None)
            img = await client.post(
                "https://api.openai.com/v1/images/generations",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json=body,
                timeout=90
            )
        if img.status_code != 200:
            print(f"[SOCIAL] image error: {img.text[:200]}")
            return None

        data = img.json()
        item = data["data"][0]
        if "b64_json" in item:
            img_bytes = base64.b64decode(item["b64_json"])
        elif "url" in item:
            r = await client.get(item["url"], timeout=30)
            img_bytes = r.content
        else:
            return None

        if logo_url:
            try:
                logo_resp = await client.get(logo_url, timeout=15)
                if logo_resp.status_code == 200:
                    base = Image.open(BytesIO(img_bytes)).convert("RGBA")
                    final = overlay_logo(base, logo_resp.content)
                    buf = BytesIO()
                    final.convert("RGB").save(buf, format="JPEG", quality=85)
                    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
            except Exception as e:
                print(f"[SOCIAL] logo: {e}")

        return "data:image/png;base64," + base64.b64encode(img_bytes).decode()
    except Exception as e:
        print(f"[SOCIAL] generate_image: {e}")
        return None

PLATFORM_LABELS = {"instagram": "Instagram", "facebook": "Facebook", "linkedin": "LinkedIn"}

async def generate_posts(client: httpx.AsyncClient, context: str, theme: str, with_emojis: bool, feedback_text: str, website: str, platforms: List[str]) -> dict:
    emoji_rule = "0 à 3 emojis maximum." if with_emojis else "Aucun emoji."
    feedback_block = f"\n\nRetours précédents à respecter :\n{feedback_text}" if feedback_text else ""
    website_info = f"\nSite : {website}" if website else ""

    user_msg = f"""Contexte activité : {context}
Thème : {theme or "aucun"}
{emoji_rule}
{website_info}
{feedback_block}

Réseaux demandés : {", ".join(PLATFORM_LABELS.get(p, p) for p in platforms)}
Génère uniquement ces versions, en JSON."""

    try:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg}
                ],
                "temperature": 0.75,
                "max_tokens": 2500,
                "response_format": {"type": "json_object"}
            },
            timeout=60
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        if isinstance(data, dict):
            data = {str(k).lower(): v for k, v in data.items()}
        
        for platform in data:
            if isinstance(data[platform], dict):
                data[platform]["caption"] = clean_text(data[platform].get("caption", ""))
                data[platform]["first_comment"] = clean_text(data[platform].get("first_comment", ""))
        
        return data
    except Exception as e:
        print(f"[SOCIAL] generate_posts: {e}")
        return {}

def _parse_json(raw: str) -> dict:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw.strip())
    if isinstance(data, dict):
        data = {str(k).lower(): v for k, v in data.items()}
    return data if isinstance(data, dict) else {}

async def _chat_json(client: httpx.AsyncClient, user_msg: str, max_tokens: int, timeout: int) -> dict:
    resp = await client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={
            "model": TEXT_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg}
            ],
            "temperature": 0.75,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"}
        },
        timeout=timeout
    )
    resp.raise_for_status()
    return _parse_json(resp.json()["choices"][0]["message"]["content"])

def _base_request(context: str, theme: str, with_emojis: bool, feedback_text: str, website: str) -> str:
    emoji_rule = "0 à 3 emojis maximum." if with_emojis else "Aucun emoji."
    feedback_block = f"\n\nRetours précédents à respecter :\n{feedback_text}" if feedback_text else ""
    website_info = f"\nSite : {website}" if website else ""
    return f"""Contexte activité : {context}
Thème : {theme or "aucun"}
{emoji_rule}
{website_info}
{feedback_block}"""

async def generate_plan(client: httpx.AsyncClient, base: str, platforms: List[str]) -> dict:
    """Étape rapide : uniquement l'accroche et le brief image de chaque réseau (petite réponse = quelques secondes).
    Permet de lancer les images tout de suite, en parallèle de la rédaction."""
    labels = ", ".join(PLATFORM_LABELS.get(p, p) for p in platforms)
    keys = ", ".join(f'"{p}"' for p in platforms)
    user_msg = f"""{base}

Réseaux demandés : {labels}
ÉTAPE 1 SEULEMENT : pour chaque réseau, donne uniquement l'accroche (1re ligne du post, 125 caractères max, un type d'accroche différent par réseau) et le brief image (une image différente par réseau).
Réponds en JSON avec les clés {keys}, chacune au format : {{"hook": "...", "brief": "..."}}"""
    return await _chat_json(client, user_msg, max_tokens=700, timeout=30)

async def generate_single_post(client: httpx.AsyncClient, base: str, platform: str, plan: dict) -> dict:
    """Étape 2 : rédige le post complet d'UN réseau à partir de son accroche. Les réseaux sont rédigés en parallèle."""
    label = PLATFORM_LABELS.get(platform, platform)
    mine = plan.get(platform, {}) if isinstance(plan.get(platform), dict) else {}
    others = [f"- {PLATFORM_LABELS.get(p, p)} : {v.get('hook', '')}" for p, v in plan.items()
              if p != platform and isinstance(v, dict) and v.get("hook")]
    others_block = ("\nAccroches des autres réseaux (ne reprends aucune de leurs phrases) :\n" + "\n".join(others)) if others else ""
    user_msg = f"""{base}

Réseau : {label} uniquement.
Accroche imposée (1re ligne, à garder telle quelle) : {mine.get('hook', '')}
Image prévue : {mine.get('brief', '')}{others_block}

Rédige le post complet pour {label} et son 1er commentaire, en respectant toutes les règles.
Réponds en JSON : {{"caption": "...", "first_comment": "..."}}"""
    data = await _chat_json(client, user_msg, max_tokens=900, timeout=45)
    # Le modèle répond parfois avec la clé du réseau autour : on accepte les deux formes
    if platform in data and isinstance(data[platform], dict):
        data = data[platform]
    return {
        "caption": clean_text(data.get("caption", "")),
        "first_comment": clean_text(data.get("first_comment", "")),
    }

class Progress:
    """Pourcentage basé sur le vrai avancement : chaque étape a un poids.
    Une étape terminée compte entièrement ; une étape en cours avance doucement selon sa durée habituelle,
    sans jamais dépasser 90 % de son poids. La barre ne fonce donc plus à 99 % pour y rester bloquée."""
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.steps = {}  # nom -> {"weight", "expected", "start", "done"}
        self.last = 0

    def add(self, name: str, weight: float, expected: float):
        self.steps[name] = {"weight": weight, "expected": expected, "start": None, "done": False}

    def start(self, name: str):
        if name in self.steps and self.steps[name]["start"] is None:
            self.steps[name]["start"] = time.monotonic()

    def done(self, name: str):
        if name in self.steps:
            self.steps[name]["done"] = True
        self.publish()

    def value(self) -> int:
        total = sum(s["weight"] for s in self.steps.values()) or 1
        acc = 0.0
        now = time.monotonic()
        for s in self.steps.values():
            if s["done"]:
                acc += s["weight"]
            elif s["start"] is not None:
                elapsed = now - s["start"]
                # Courbe douce : ~63 % du poids à la durée habituelle, puis ralentit, plafonnée à 90 %
                frac = min(0.9, 1 - math.exp(-elapsed / max(s["expected"], 0.1)))
                acc += s["weight"] * frac
        return min(99, int(acc / total * 100))

    def publish(self):
        v = max(self.last, self.value())  # ne recule jamais
        self.last = v
        job = JOBS.get(self.job_id)
        if job is not None and job.get("status") != "done":
            job["progress"] = v

    async def run(self):
        try:
            while True:
                self.publish()
                await asyncio.sleep(0.4)
        except asyncio.CancelledError:
            pass

async def run_generation(job_id: str, payload):
    JOBS[job_id] = {"status": "starting", "progress": 0, "result": None, "error": None}
    
    try:
        user_id = payload.user_id
        theme = (payload.theme or "").strip()
        if theme == "Autre (précisez)":
            theme = (payload.theme_other or "").strip()
        platforms = [p.lower() for p in (payload.platforms or ["instagram"])]
        with_emojis = payload.with_emojis if payload.with_emojis is not None else True

        context = "activité non précisée"
        logo_url = payload.logo_url
        website = ""
        if supabase:
            try:
                res = supabase.table("profiles").select("social_context, social_logo_url, website").eq("id", user_id).single().execute()
                if res.data:
                    context = res.data.get("social_context") or context
                    logo_url = logo_url or res.data.get("social_logo_url")
                    website = (res.data.get("website") or "").strip()
            except Exception as e:
                print(f"[SOCIAL] profile: {e}")

        feedback_text = get_recent_feedback(user_id)

        async with httpx.AsyncClient() as client:
            base = _base_request(context, theme, with_emojis, feedback_text, website)
            fallback_brief = "Artisan au travail sur un vrai chantier, photo réaliste prise au smartphone, lumière naturelle"

            # Poids des étapes (le pourcentage suit le vrai avancement)
            progress = Progress(job_id)
            progress.add("plan", weight=10, expected=4)
            for p in platforms:
                progress.add(f"text_{p}", weight=10, expected=8)
                progress.add(f"image_{p}", weight=25, expected=20)
            ticker = asyncio.create_task(progress.run())

            try:
                # Étape 1 (rapide) : accroches + briefs image
                JOBS[job_id]["status"] = "writing_text"
                progress.start("plan")
                plan = {}
                try:
                    plan = await generate_plan(client, base, platforms)
                except Exception as e:
                    print(f"[SOCIAL] plan: {e}")
                progress.done("plan")

                if plan:
                    # Étape 2 : TOUT en parallèle — images + rédaction de chaque réseau
                    JOBS[job_id]["status"] = "generating_image"
                    briefs = {p: ((plan.get(p) or {}).get("brief") if isinstance(plan.get(p), dict) else None) or fallback_brief
                              for p in platforms}

                    async def do_image(p):
                        progress.start(f"image_{p}")
                        try:
                            return await generate_image(client, briefs[p], logo_url)
                        finally:
                            progress.done(f"image_{p}")

                    async def do_text(p):
                        progress.start(f"text_{p}")
                        try:
                            return await generate_single_post(client, base, p, plan)
                        except Exception as e:
                            print(f"[SOCIAL] text {p}: {e}")
                            return {"caption": "", "first_comment": ""}
                        finally:
                            progress.done(f"text_{p}")

                    results = await asyncio.gather(
                        *[do_image(p) for p in platforms],
                        *[do_text(p) for p in platforms],
                        return_exceptions=True
                    )
                    n = len(platforms)
                    image_urls = [r if isinstance(r, str) else None for r in results[:n]]
                    texts = [r if isinstance(r, dict) else {"caption": "", "first_comment": ""} for r in results[n:]]
                    posts_data = {p: {"brief": briefs[p], **texts[i]} for i, p in enumerate(platforms)}
                else:
                    # Repli : ancienne méthode (tout le texte d'un coup, puis les images en parallèle)
                    for p in platforms:
                        progress.start(f"text_{p}")
                    posts_data = await generate_posts(client, context, theme, with_emojis, feedback_text, website, platforms)
                    for p in platforms:
                        progress.done(f"text_{p}")
                    JOBS[job_id]["status"] = "generating_image"
                    briefs = {p: (posts_data.get(p, {}) or {}).get("brief") or fallback_brief for p in platforms}

                    async def do_image_fb(p):
                        progress.start(f"image_{p}")
                        try:
                            return await generate_image(client, briefs[p], logo_url)
                        finally:
                            progress.done(f"image_{p}")

                    res = await asyncio.gather(*[do_image_fb(p) for p in platforms], return_exceptions=True)
                    image_urls = [r if isinstance(r, str) else None for r in res]
            finally:
                ticker.cancel()

            posts = []
            for platform, image_url in zip(platforms, image_urls):
                p_data = posts_data.get(platform, {}) or {}
                posts.append({
                    "platform": platform,
                    "caption": p_data.get("caption", ""),
                    "first_comment": p_data.get("first_comment", ""),
                    "image_brief": p_data.get("brief", fallback_brief),
                    "image_url": image_url,
                    "image_urls": [image_url] if image_url else []
                })

            JOBS[job_id] = {
                "status": "done",
                "progress": 100,
                "result": {
                    "success": True,
                    "posts": posts,
                    "image_urls": [p["image_url"] for p in posts if p.get("image_url")],
                    "carousel": False
                },
                "error": None
            }

    except Exception as e:
        print(f"[SOCIAL] job {job_id} error: {e}")
        JOBS[job_id] = {"status": "error", "progress": 0, "result": None, "error": str(e)}

class GeneratePayload(BaseModel):
    user_id: str
    theme: Optional[str] = ""
    theme_other: Optional[str] = ""
    platforms: List[str] = ["instagram"]
    with_emojis: Optional[bool] = True
    logo_url: Optional[str] = None

class FeedbackPayload(BaseModel):
    user_id: str
    theme: Optional[str] = ""
    liked: bool
    comment: Optional[str] = ""

@router.post("/social/generate")
async def start_generate(payload: GeneratePayload, background_tasks: BackgroundTasks):
    if not OPENAI_API_KEY:
        return {"success": False, "message": "Clé OpenAI manquante."}
    
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "starting", "progress": 0, "result": None, "error": None}
    background_tasks.add_task(run_generation, job_id, payload)
    return {"success": True, "job_id": job_id}

@router.get("/social/generate/status")
async def get_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return {"success": False, "message": "Job introuvable"}
    return {
        "success": True,
        "status": job["status"],
        "progress": job["progress"],
        "result": job["result"],
        "error": job["error"]
    }

@router.get("/social/advice")
async def get_social_advice():
    if not supabase:
        return {"success": False, "advice": "Conseils non disponibles.", "updated_at": None}
    try:
        res = supabase.table("social_advice").select("content, updated_at").eq("id", 1).single().execute()
        if res.data:
            return {"success": True, "advice": res.data.get("content") or "", "updated_at": res.data.get("updated_at")}
    except Exception as e:
        print(f"[SOCIAL] advice: {e}")
    return {"success": False, "advice": "Conseils non disponibles.", "updated_at": None}

@router.post("/social/feedback")
async def save_feedback(payload: FeedbackPayload):
    if not supabase:
        return {"success": False, "message": "Supabase non configuré."}
    try:
        supabase.table("social_feedback").insert({
            "user_id": payload.user_id,
            "theme": payload.theme or "",
            "liked": payload.liked,
            "comment": payload.comment or ""
        }).execute()
        return {"success": True}
    except Exception as e:
        print(f"[SOCIAL] feedback save: {e}")
        return {"success": False, "message": "Erreur enregistrement."}

@router.post("/social/logo")
async def upload_logo(user_id: str = Form(...), file: UploadFile = File(...)):
    if not supabase:
        return {"success": False, "message": "Supabase non configuré."}
    try:
        content = await file.read()
        path = f"{user_id}/logo.png"
        supabase.storage.from_("social-logos").upload(
            path, content, {"content-type": file.content_type or "image/png", "upsert": "true"}
        )
        public_url = supabase.storage.from_("social-logos").get_public_url(path)
        supabase.table("profiles").update({"social_logo_url": public_url}).eq("id", user_id).execute()
        return {"success": True, "logo_url": public_url}
    except Exception as e:
        print(f"[SOCIAL] logo: {e}")
        return {"success": False, "message": "Erreur logo."}
