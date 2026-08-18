# Clarity Backend – Remplacement Make (version sécurisée)

Ce backend remplace tes webhooks Make **sans toucher au design** de ton application.

## Sécurité
- Ton code actuel n’est **pas modifié**.
- Tu changes seulement 2 URLs dans `dashboard.html`.
- Si ça ne marche pas → tu remets les anciennes URLs Make → tout refonctionne immédiatement.

---

## 1. Installation (une seule fois)

### Prérequis
- Python 3.10 ou plus (installe-le depuis python.org si besoin)
- Une clé OpenAI (tu peux en créer une sur platform.openai.com)

### Étapes

```bash
# 1. Va dans le dossier
cd clarity-backend

# 2. Crée un environnement virtuel
python -m venv venv

# 3. Active-le
# Sur Windows :
venv\Scripts\activate
# Sur Mac / Linux :
source venv/bin/activate

# 4. Installe les dépendances
pip install -r requirements.txt

# 5. Configure tes clés
cp .env.example .env
# Puis ouvre le fichier .env et mets ta clé OPENAI_API_KEY
```

---

## 2. Lancer le serveur

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Tu dois voir :
```
Uvicorn running on http://0.0.0.0:8000
```

Teste dans ton navigateur : http://localhost:8000/health  
→ doit afficher `{"status":"healthy"}`

---

## 3. Brancher ton frontend (le seul changement)

Ouvre ton fichier `dashboard.html` et cherche ces lignes :

```js
const ORCHESTRATOR_WEBHOOK_URL = "https://hook.eu1.make.com/qgixxebcacs27wfchphxg6bo5ffxhlry";
const AGENT_MAIL_WEBHOOK_URL = "https://hook.eu1.make.com/f1ygm8lv4du3nnv5urqex5kxef9xuvit";
```

**Remplace-les temporairement par :**

```js
const ORCHESTRATOR_WEBHOOK_URL = "http://localhost:8000/orchestrator";
const AGENT_MAIL_WEBHOOK_URL = "http://localhost:8000/agent/mail";
```

Sauvegarde, recharge la page, et teste une dictée / un mail.

---

## 4. Si ça ne marche pas

Remets simplement les anciennes URLs Make.
Tout redevient comme avant en 10 secondes.

---

## Prochaines améliorations (quand la base marche)

- Recherche de contact dans Supabase
- Envoi réel via Gmail / Outlook (en utilisant tes tokens déjà existants)
- Agent Planning
- Déploiement en ligne (Railway / Render – gratuit au début)

---

## Structure du projet

```
clarity-backend/
├── app/
│   ├── main.py              ← Serveur FastAPI
│   ├── orchestrator.py      ← Chef d’orchestre
│   └── agents/
│       └── mail_agent.py    ← Agent Mail
├── .env.example
├── requirements.txt
└── README.md
```
