# 🏆 BENY-JOE TACTI-BALL PRO v2.0

Plateforme d'analyse tactique football IA — par BENY-JOE

## 🚀 Fonctionnalités

| Feature | Description |
|---|---|
| 🔴 Live Match | Analyse temps réel avec OCR caméra ou stream URL |
| 🎬 Analyse Vidéo | Upload MP4/AVI/MOV ou URL YouTube/YouTube Live |
| 🏟 Terrain 3D | Terrain interactif avec joueurs et ballon animés |
| 👁 OCR Joueurs | Détection numéro maillot + couleur équipe en temps réel |
| 📊 Dashboard Stats | Toutes les stats du match en direct |
| 🧠 Coach IA | Chat avec TACTI-BOT expert football |
| ↔ Passes Network | Analyse réseau de passes |
| 🌡 Heatmaps | Zones de chaleur par joueur/équipe |

## 📁 Structure

```
tactiball-pro/
├── backend/
│   ├── main.py              # API FastAPI principale
│   ├── requirements.txt     # Dépendances Python
│   └── .env.example         # Variables d'environnement
├── frontend/
│   └── index.html           # Interface complète (SPA)
└── README.md
```

## ⚙️ Installation Backend (Render.com — GRATUIT)

### 1. Préparer les clés Groq (GRATUITES)
1. Allez sur https://console.groq.com
2. Créez un compte gratuit
3. Créez 2 clés API (pour la rotation)
4. Copiez vos clés

### 2. Déployer sur Render
1. Uploadez le dossier `backend/` sur GitHub
2. Allez sur https://render.com
3. Nouveau service → Web Service → connectez votre repo
4. Paramètres :
   - **Build Command** : `pip install -r requirements.txt`
   - **Start Command** : `uvicorn main:app --host 0.0.0.0 --port 10000`
5. Variables d'environnement :
   - `GROQ_API_KEY` = votre clé Groq principale
   - `GROQ_API_KEY_2` = votre 2e clé Groq (optionnel)
   - `RENDER_EXTERNAL_URL` = https://votre-app.onrender.com

### 3. Déployer le Frontend (InfinityFree, Netlify, etc.)
1. Éditez `frontend/index.html` ligne 2 :
   ```javascript
   const API = 'https://VOTRE-APP.onrender.com';
   ```
2. Uploadez `index.html` sur votre hébergeur

## 🎮 Utilisation

### Live Match avec OCR Caméra
1. Onglet **LIVE** → configurez les équipes
2. Cliquez **Démarrer le Match Live**
3. Cliquez **Activer Caméra OCR**
4. Pointez la caméra vers l'écran du match
5. L'IA détecte automatiquement les joueurs, score et minute

### Live depuis URL Stream
1. Entrez l'URL YouTube Live dans le champ "Source Live"
2. Démarrez — l'IA analyse le stream

### Analyse Vidéo
1. Onglet **Vidéo**
2. Upload un fichier ou entrez une URL YouTube
3. L'IA génère l'analyse tactique complète + données terrain

### Terrain 3D
1. Onglet **Terrain 3D**
2. Choisissez les formations
3. Cliquez **Animer** pour voir les joueurs se déplacer
4. Activez Heatmap, Passes, changez la vue

## 🔑 Clés API (toutes gratuites)

| Service | URL | Limite gratuite |
|---|---|---|
| Groq | https://console.groq.com | ~14,400 req/jour |
| Render | https://render.com | 750h/mois |
| InfinityFree | https://infinityfree.net | Illimité |

## 📡 API Endpoints

```
GET  /health                    — Statut API
POST /api/video/upload          — Analyse vidéo (FormData)
POST /api/video/url             — Analyse URL vidéo
POST /api/ocr/frame             — OCR frame base64
POST /api/live/start            — Démarrer match live
POST /api/live/stop             — Arrêter match live
GET  /api/live/state            — État match live
POST /api/live/event            — Ajouter événement
POST /api/live/update_players   — Positions joueurs
POST /api/tactical-analysis     — Analyse tactique
POST /api/match-report          — Rapport de match
POST /api/player-analysis       — Analyse joueur
POST /api/coach-chat            — Chat TACTI-BOT
POST /api/training-plan         — Plan entraînement
POST /api/formation-suggest     — Suggestion formation
POST /api/passes/analyze        — Analyse passes
POST /api/heatmap/analyze       — Analyse heatmap
WS   /ws/live                   — WebSocket live
```

---
*BENY-JOE TACTI-BALL PRO v2.0 — Analyse Tactique Football IA*
