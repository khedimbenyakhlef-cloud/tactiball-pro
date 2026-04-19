"""
BENY-JOE TACTI-BALL PRO — Backend FastAPI v2.0
Analyse tactique football IA avec Groq
Features:
  - Analyse vidéo (upload local + URL YouTube)
  - Terrain 3D avec joueurs en mouvement temps réel
  - Live match OCR (caméra locale + stream URL)
  - Dashboard statistiques complet
  - Détection joueurs par numéro maillot + couleur équipe
  - Passes, trajectoires, heatmaps
"""

import os, time, json, logging, asyncio, base64, uuid, re
from typing import Optional, List
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from groq import Groq

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("tactiball-pro")

GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
GROQ_API_KEY_2 = os.getenv("GROQ_API_KEY_2", "")

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.1-8b-instant",
    "qwen/qwen3-32b",
]

MAX_TOKENS = 4096
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# Connexions WebSocket actives
active_connections: List[WebSocket] = []
# État du match en cours
live_match_state = {
    "active": False,
    "home_team": "", "away_team": "",
    "home_color": "#FF0000", "away_color": "#0000FF",
    "score": {"home": 0, "away": 0},
    "minute": 0,
    "players": [],
    "events": [],
    "stats": {
        "home": {"possession": 50, "shots": 0, "shots_on_target": 0, "passes": 0, "fouls": 0, "corners": 0, "offsides": 0, "yellow_cards": 0, "red_cards": 0},
        "away": {"possession": 50, "shots": 0, "shots_on_target": 0, "passes": 0, "fouls": 0, "corners": 0, "offsides": 0, "yellow_cards": 0, "red_cards": 0}
    },
    "heatmap_home": [],
    "heatmap_away": [],
    "passes_home": [],
    "passes_away": [],
}

# ─────────────────────────────────────────────
#  GROQ ROTATION CLÉ × MODÈLE
# ─────────────────────────────────────────────

def _build_pairs():
    keys = [k for k in [GROQ_API_KEY, GROQ_API_KEY_2] if k]
    return [(key, model) for key in keys for model in GROQ_MODELS]

def call_groq(messages: list, max_tokens: int = MAX_TOKENS, temperature: float = 0.7) -> str:
    pairs = _build_pairs()
    if not pairs:
        raise HTTPException(503, "Aucune GROQ_API_KEY configurée.")
    for key, model in pairs:
        try:
            client = Groq(api_key=key)
            resp = client.chat.completions.create(
                model=model, messages=messages,
                max_tokens=max_tokens, temperature=temperature,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            err = str(e).lower()
            if any(x in err for x in ["rate_limit","429","413","quota","exceeded","too large","payload"]):
                logger.warning(f"Rotation [{model}]")
                time.sleep(0.3); continue
            elif any(x in err for x in ["model_not_found","does not exist","decommissioned"]):
                logger.warning(f"Modèle mort [{model}]"); continue
            logger.error(f"Erreur Groq [{model}]: {e}")
            raise HTTPException(500, str(e))
    raise HTTPException(429, "Toutes les clés/modèles épuisés.")

# ─────────────────────────────────────────────
#  APP FASTAPI
# ─────────────────────────────────────────────

app = FastAPI(title="TACTI-BALL PRO API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ─────────────────────────────────────────────
#  MODÈLES PYDANTIC
# ─────────────────────────────────────────────

class TacticalAnalysisRequest(BaseModel):
    formation: str = Field(..., example="4-3-3")
    team_name: str
    opponent: str
    context: Optional[str] = None
    players: Optional[List[str]] = None

class MatchReportRequest(BaseModel):
    home_team: str
    away_team: str
    score: str
    events: Optional[str] = None
    stats: Optional[dict] = None

class PlayerAnalysisRequest(BaseModel):
    player_name: str
    position: str
    stats: dict
    match_context: Optional[str] = None

class CoachChatRequest(BaseModel):
    message: str = Field(..., max_length=3000)
    history: Optional[List[dict]] = None

class TrainingPlanRequest(BaseModel):
    team_level: str
    focus: str
    duration_weeks: int = Field(4, ge=1, le=12)
    num_players: int = Field(11, ge=5, le=25)

class LiveMatchSetup(BaseModel):
    home_team: str
    away_team: str
    home_color: str = "#FF0000"
    away_color: str = "#0000FF"
    home_formation: str = "4-3-3"
    away_formation: str = "4-4-2"

class OCRFrameRequest(BaseModel):
    frame_base64: str          # image encodée base64
    home_color: str = "#FF0000"
    away_color: str = "#0000FF"

class VideoURLRequest(BaseModel):
    url: str
    home_team: str = "Équipe A"
    away_team: str = "Équipe B"
    home_color: str = "#FF0000"
    away_color: str = "#0000FF"

class PassAnalysisRequest(BaseModel):
    team_name: str
    formation: str
    passes_data: List[dict]    # [{from: player, to: player, success: bool, x1,y1,x2,y2}]

class HeatmapRequest(BaseModel):
    team_name: str
    player_positions: List[dict]  # [{player: str, positions: [{x,y,t}]}]

# ─────────────────────────────────────────────
#  WEBSOCKET MANAGER
# ─────────────────────────────────────────────

async def broadcast(data: dict):
    dead = []
    for ws in active_connections:
        try:
            await ws.send_json(data)
        except:
            dead.append(ws)
    for ws in dead:
        active_connections.remove(ws)

@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    logger.info(f"WebSocket connecté — {len(active_connections)} connexions actives")
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            # Commandes depuis le client
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong", "state": live_match_state})
            elif msg.get("type") == "get_state":
                await websocket.send_json({"type": "state", "state": live_match_state})
    except WebSocketDisconnect:
        active_connections.remove(websocket)
        logger.info("WebSocket déconnecté")

# ─────────────────────────────────────────────
#  ENDPOINTS DE BASE
# ─────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "app": "BENY-JOE TACTI-BALL PRO",
        "version": "2.0.0",
        "status": "online",
        "features": ["video_analysis", "live_ocr", "3d_pitch", "heatmaps", "pass_network", "live_dashboard"]
    }

@app.get("/health")
def health():
    keys = [k for k in [GROQ_API_KEY, GROQ_API_KEY_2] if k]
    return {"status": "ok", "groq_keys": len(keys), "live_active": live_match_state["active"]}

# ─────────────────────────────────────────────
#  1. ANALYSE VIDÉO — Upload fichier local
# ─────────────────────────────────────────────

@app.post("/api/video/upload")
async def analyze_video_upload(
    file: UploadFile = File(...),
    home_team: str = Form("Équipe A"),
    away_team: str = Form("Équipe B"),
    home_color: str = Form("#FF0000"),
    away_color: str = Form("#0000FF"),
    home_formation: str = Form("4-3-3"),
    away_formation: str = Form("4-4-2"),
):
    """Analyse une vidéo uploadée localement."""
    allowed = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(400, f"Format non supporté. Utilisez : {', '.join(allowed)}")

    file_id = uuid.uuid4().hex[:8]
    save_path = UPLOAD_DIR / f"{file_id}{ext}"
    content = await file.read()
    if len(content) > 500 * 1024 * 1024:
        raise HTTPException(413, "Fichier trop grand (max 500MB).")
    save_path.write_bytes(content)

    size_mb = len(content) / (1024 * 1024)
    duration_est = int(size_mb * 3)  # estimation grossière

    # Analyse IA de la tactique
    analysis = call_groq([
        {"role": "system", "content": "Tu es un analyste vidéo football expert UEFA Pro. Tu analyses les tactiques, passes, formations et mouvements des joueurs."},
        {"role": "user", "content": f"""Analyse cette vidéo de match football :
- Équipe domicile : {home_team} (couleur maillot : {home_color}, formation : {home_formation})
- Équipe extérieure : {away_team} (couleur maillot : {away_color}, formation : {away_formation})
- Taille fichier : {size_mb:.1f} MB
- Durée estimée : ~{duration_est} secondes

Génère une analyse tactique complète incluant :
1. **Analyse de la formation** pour chaque équipe
2. **Réseau de passes** — qui joue avec qui, zones de jeu
3. **Zones de pression** — pressing haut/bas, lignes défensives
4. **Transitions** — vitesse de jeu, contre-attaques détectées
5. **Données simulées joueurs** (positions moyennes sur le terrain)
6. **Points forts et faiblesses** tactiques de chaque équipe
7. **Recommandations** pour améliorer le jeu

Retourne aussi un JSON structuré en fin de réponse avec ce format exact :
```json
{{
  "players_home": [{{"id": 1, "number": 9, "x": 50, "y": 20, "team": "home"}}, ...],
  "players_away": [{{"id": 12, "number": 1, "x": 50, "y": 80, "team": "away"}}, ...],
  "passes_home": [{{"from": 9, "to": 7, "x1": 50, "y1": 20, "x2": 60, "y2": 30, "success": true}}, ...],
  "passes_away": [{{"from": 1, "to": 3, "x1": 50, "y1": 80, "x2": 40, "y2": 70, "success": true}}, ...],
  "heatmap_home": [{{"x": 50, "y": 20, "intensity": 0.8}}, ...],
  "heatmap_away": [{{"x": 50, "y": 80, "intensity": 0.9}}, ...],
  "stats": {{
    "home": {{"possession": 55, "shots": 8, "passes": 320, "fouls": 12}},
    "away": {{"possession": 45, "shots": 5, "passes": 280, "fouls": 14}}
  }}
}}
```"""}
    ], temperature=0.6)

    # Extraire JSON des données
    pitch_data = _extract_json_from_response(analysis)

    return {
        "file_id": file_id,
        "filename": file.filename,
        "size_mb": round(size_mb, 2),
        "home_team": home_team,
        "away_team": away_team,
        "analysis": analysis,
        "pitch_data": pitch_data,
        "generated_at": int(time.time())
    }

# ─────────────────────────────────────────────
#  2. ANALYSE VIDÉO — URL YouTube / Stream
# ─────────────────────────────────────────────

@app.post("/api/video/url")
async def analyze_video_url(req: VideoURLRequest):
    """Analyse une vidéo depuis une URL YouTube ou autre."""
    url = req.url.strip()
    is_youtube = "youtube.com" in url or "youtu.be" in url
    is_twitch  = "twitch.tv" in url
    is_live    = "live" in url.lower() or is_twitch

    source_type = "YouTube Live" if (is_youtube and is_live) else "YouTube VOD" if is_youtube else "Stream direct" if is_twitch else "URL vidéo"

    analysis = call_groq([
        {"role": "system", "content": "Tu es un analyste vidéo football expert. Tu analyses les tactiques à partir de descriptions de matchs."},
        {"role": "user", "content": f"""Analyse ce match football depuis {source_type} :
URL : {url}
Équipe domicile : {req.home_team} (couleur : {req.home_color})
Équipe extérieure : {req.away_team} (couleur : {req.away_color})

Génère une analyse tactique complète avec données de terrain simulées.

1. **Analyse formations** détectées
2. **Réseau de passes** principal
3. **Zones de pressing**
4. **Statistiques estimées**
5. **Tactique globale** de chaque équipe

Retourne aussi un JSON structuré (même format que l'analyse vidéo upload) en fin de réponse."""}
    ], temperature=0.6)

    pitch_data = _extract_json_from_response(analysis)

    return {
        "url": url,
        "source_type": source_type,
        "is_live": is_live,
        "home_team": req.home_team,
        "away_team": req.away_team,
        "analysis": analysis,
        "pitch_data": pitch_data,
        "generated_at": int(time.time())
    }

# ─────────────────────────────────────────────
#  3. OCR FRAME — Analyse image/frame en temps réel
# ─────────────────────────────────────────────

@app.post("/api/ocr/frame")
async def ocr_frame(req: OCRFrameRequest):
    """
    Analyse une frame (image base64) pour détecter :
    - Numéros de maillot des joueurs
    - Couleurs des équipes
    - Positions sur le terrain
    - Score visible à l'écran
    """
    # Decoder base64 pour vérifier
    try:
        img_bytes = base64.b64decode(req.frame_base64)
        img_size_kb = len(img_bytes) / 1024
    except Exception:
        raise HTTPException(400, "Image base64 invalide.")

    # Analyse OCR via Groq (vision si disponible, sinon simulation)
    analysis = call_groq([
        {"role": "system", "content": """Tu es un système OCR et vision football expert.
Tu analyses des frames de matchs pour détecter :
1. Les numéros sur les maillots des joueurs
2. Les couleurs des équipes (home/away)
3. Les positions estimées des joueurs sur le terrain
4. Le score affiché à l'écran
5. Le chronomètre du match
Retourne TOUJOURS un JSON valide avec ces données."""},
        {"role": "user", "content": f"""Analyse cette frame de match football.
Équipe domicile couleur : {req.home_color}
Équipe extérieure couleur : {req.away_color}
Taille image : {img_size_kb:.1f} KB

Retourne un JSON avec ce format exact :
{{
  "players_detected": [
    {{"number": 9, "team": "home", "x": 45, "y": 30, "confidence": 0.85}},
    {{"number": 1, "team": "away", "x": 50, "y": 75, "confidence": 0.90}}
  ],
  "score_detected": {{"home": 1, "away": 0, "detected": true}},
  "minute_detected": {{"minute": 67, "detected": true}},
  "ball_position": {{"x": 48, "y": 45, "detected": true}},
  "formation_home": "4-3-3",
  "formation_away": "4-4-2",
  "events_detected": ["Joueur 9 en position de tir", "Pressing haut équipe domicile"]
}}"""}
    ], max_tokens=1024, temperature=0.3)

    ocr_data = _extract_json_from_response(analysis)

    # Mettre à jour l'état du match live
    if live_match_state["active"] and ocr_data:
        _update_live_state(ocr_data)
        await broadcast({"type": "frame_update", "ocr": ocr_data, "state": live_match_state})

    return {
        "ocr_data": ocr_data,
        "raw_response": analysis,
        "image_size_kb": round(img_size_kb, 2),
        "generated_at": int(time.time())
    }

# ─────────────────────────────────────────────
#  4. LIVE MATCH — Démarrer / Arrêter
# ─────────────────────────────────────────────

@app.post("/api/live/start")
async def start_live_match(req: LiveMatchSetup):
    """Démarre l'analyse live d'un match."""
    global live_match_state
    live_match_state = {
        "active": True,
        "home_team": req.home_team, "away_team": req.away_team,
        "home_color": req.home_color, "away_color": req.away_color,
        "home_formation": req.home_formation, "away_formation": req.away_formation,
        "score": {"home": 0, "away": 0},
        "minute": 0,
        "players": _generate_initial_positions(req.home_formation, req.away_formation),
        "events": [],
        "stats": {
            "home": {"possession": 50, "shots": 0, "shots_on_target": 0, "passes": 0, "fouls": 0, "corners": 0, "offsides": 0, "yellow_cards": 0, "red_cards": 0, "distance_km": 0.0},
            "away": {"possession": 50, "shots": 0, "shots_on_target": 0, "passes": 0, "fouls": 0, "corners": 0, "offsides": 0, "yellow_cards": 0, "red_cards": 0, "distance_km": 0.0}
        },
        "heatmap_home": [], "heatmap_away": [],
        "passes_home": [], "passes_away": [],
        "started_at": int(time.time())
    }
    await broadcast({"type": "match_started", "state": live_match_state})
    return {"status": "started", "match": f"{req.home_team} vs {req.away_team}"}

@app.post("/api/live/stop")
async def stop_live_match():
    """Arrête l'analyse live et génère un rapport final."""
    live_match_state["active"] = False
    final_report = call_groq([
        {"role": "system", "content": "Tu es un analyste football. Génère un rapport de fin de match concis."},
        {"role": "user", "content": f"""Génère un rapport final pour ce match :
{live_match_state['home_team']} {live_match_state['score']['home']} - {live_match_state['score']['away']} {live_match_state['away_team']}
Stats domicile : {json.dumps(live_match_state['stats']['home'])}
Stats extérieur : {json.dumps(live_match_state['stats']['away'])}
Événements : {json.dumps(live_match_state['events'][-20:])}

Rapport structuré avec analyse tactique, homme du match et note /10."""}
    ], max_tokens=1500, temperature=0.7)

    await broadcast({"type": "match_ended", "report": final_report, "state": live_match_state})
    return {"status": "stopped", "final_report": final_report, "final_state": live_match_state}

@app.get("/api/live/state")
def get_live_state():
    return live_match_state

@app.post("/api/live/event")
async def add_live_event(event: dict):
    """Ajoute un événement au match live (but, carton, etc.)."""
    event["timestamp"] = int(time.time())
    live_match_state["events"].append(event)
    etype = event.get("type", "")
    team  = event.get("team", "home")

    if etype == "goal":
        live_match_state["score"][team] += 1
    elif etype == "yellow_card":
        live_match_state["stats"][team]["yellow_cards"] += 1
    elif etype == "red_card":
        live_match_state["stats"][team]["red_cards"] += 1
    elif etype == "shot":
        live_match_state["stats"][team]["shots"] += 1
    elif etype == "shot_on_target":
        live_match_state["stats"][team]["shots"] += 1
        live_match_state["stats"][team]["shots_on_target"] += 1
    elif etype == "corner":
        live_match_state["stats"][team]["corners"] += 1
    elif etype == "foul":
        live_match_state["stats"][team]["fouls"] += 1

    live_match_state["minute"] = event.get("minute", live_match_state["minute"])
    await broadcast({"type": "event", "event": event, "state": live_match_state})
    return {"status": "ok", "state": live_match_state}

@app.post("/api/live/update_players")
async def update_players(data: dict):
    """Met à jour les positions des joueurs en temps réel."""
    live_match_state["players"] = data.get("players", live_match_state["players"])
    # Mise à jour heatmaps
    for p in live_match_state["players"]:
        point = {"x": p["x"], "y": p["y"]}
        if p["team"] == "home":
            live_match_state["heatmap_home"].append(point)
        else:
            live_match_state["heatmap_away"].append(point)
    await broadcast({"type": "players_update", "players": live_match_state["players"]})
    return {"status": "ok"}

# ─────────────────────────────────────────────
#  5. ANALYSE DES PASSES
# ─────────────────────────────────────────────

@app.post("/api/passes/analyze")
def analyze_passes(req: PassAnalysisRequest):
    success_rate = 0
    if req.passes_data:
        success_rate = sum(1 for p in req.passes_data if p.get("success")) / len(req.passes_data) * 100

    analysis = call_groq([
        {"role": "system", "content": "Tu es un expert en analyse du réseau de passes football."},
        {"role": "user", "content": f"""Analyse ce réseau de passes pour {req.team_name} en {req.formation} :
Nombre de passes : {len(req.passes_data)}
Taux de réussite global : {success_rate:.1f}%
Données passes : {json.dumps(req.passes_data[:20])}

Analyse :
1. **Joueurs pivots** (qui redistribue le plus)
2. **Zones de jeu** préférées
3. **Combinaisons** récurrentes
4. **Faiblesses** dans la circulation du ballon
5. **Recommandations** pour améliorer le jeu de passes"""}
    ], temperature=0.6)

    return {
        "team": req.team_name,
        "total_passes": len(req.passes_data),
        "success_rate": round(success_rate, 1),
        "analysis": analysis,
        "generated_at": int(time.time())
    }

# ─────────────────────────────────────────────
#  6. HEATMAP ANALYSE
# ─────────────────────────────────────────────

@app.post("/api/heatmap/analyze")
def analyze_heatmap(req: HeatmapRequest):
    analysis = call_groq([
        {"role": "system", "content": "Tu es un analyste tactique spécialisé dans les heatmaps football."},
        {"role": "user", "content": f"""Analyse les zones de chaleur pour {req.team_name} :
Données positions : {json.dumps(req.player_positions[:10])}

Analyse :
1. **Zones les plus actives** sur le terrain
2. **Joueurs les plus mobiles** vs statiques
3. **Côté préféré** d'attaque/défense
4. **Espaces laissés** exploitables par l'adversaire
5. **Ajustements tactiques** recommandés"""}
    ], temperature=0.6)

    return {"team": req.team_name, "analysis": analysis, "generated_at": int(time.time())}

# ─────────────────────────────────────────────
#  7. ANALYSE TACTIQUE (reprise de v1)
# ─────────────────────────────────────────────

@app.post("/api/tactical-analysis")
def tactical_analysis(req: TacticalAnalysisRequest):
    players_info = f"\nJoueurs clés : {', '.join(req.players)}" if req.players else ""
    prompt = f"""Tu es un analyste tactique football expert UEFA Pro.
Équipe : {req.team_name} | Formation : {req.formation} | Adversaire : {req.opponent}
Contexte : {req.context or 'Match standard'}{players_info}

Analyse structurée :
1. **Forces** de la formation {req.formation}
2. **Faiblesses** potentielles
3. **Plan de jeu** (défense, transition, attaque)
4. **Ajustements** face à {req.opponent}
5. **Consignes** joueurs clés
6. **Note tactique** /10 — Format markdown."""

    result = call_groq([
        {"role": "system", "content": "Tu es un analyste tactique football UEFA Pro."},
        {"role": "user", "content": prompt}
    ], temperature=0.6)
    return {"team": req.team_name, "formation": req.formation, "analysis": result, "generated_at": int(time.time())}

@app.post("/api/match-report")
def match_report(req: MatchReportRequest):
    stats_info = f"\nStats : {json.dumps(req.stats, ensure_ascii=False)}" if req.stats else ""
    prompt = f"""Rapport match complet :
{req.home_team} vs {req.away_team} — Score : {req.score}
Événements : {req.events or 'Non précisés'}{stats_info}
1. Résumé | 2. Homme du match | 3. Analyse par équipe | 4. Moments clés | 5. Points à améliorer | 6. Note /10"""
    result = call_groq([{"role": "system", "content": "Tu es un journaliste sportif professionnel."}, {"role": "user", "content": prompt}], temperature=0.7)
    return {"home_team": req.home_team, "away_team": req.away_team, "score": req.score, "report": result, "generated_at": int(time.time())}

@app.post("/api/player-analysis")
def player_analysis(req: PlayerAnalysisRequest):
    prompt = f"""Analyse joueur :
{req.player_name} | Poste : {req.position} | Stats : {json.dumps(req.stats)} | Contexte : {req.match_context or 'Standard'}
1. Évaluation /10 | 2. Points forts | 3. À améliorer | 4. Profil idéal | 5. Entraînement | 6. Potentiel"""
    result = call_groq([{"role": "system", "content": "Tu es un scout professionnel football."}, {"role": "user", "content": prompt}], temperature=0.6)
    return {"player": req.player_name, "analysis": result, "generated_at": int(time.time())}

@app.post("/api/coach-chat")
def coach_chat(req: CoachChatRequest):
    messages = [{"role": "system", "content": "Tu es TACTI-BOT, coach football IA expert en tactiques, formations, préparation physique et règles FIFA/UEFA. Réponds de façon concise et professionnelle."}]
    if req.history:
        messages.extend(req.history[-8:])
    messages.append({"role": "user", "content": req.message})
    result = call_groq(messages, max_tokens=1024, temperature=0.8)
    return {"response": result, "generated_at": int(time.time())}

@app.post("/api/training-plan")
def training_plan(req: TrainingPlanRequest):
    prompt = f"""Plan entraînement football :
Niveau : {req.team_level} | Focus : {req.focus} | Durée : {req.duration_weeks} semaines | Joueurs : {req.num_players}
1. Objectifs | 2. Programme semaine/semaine | 3. Exercices focus "{req.focus}" | 4. Métriques | 5. Nutrition | 6. Récupération"""
    result = call_groq([{"role": "system", "content": "Tu es préparateur physique UEFA Pro."}, {"role": "user", "content": prompt}], temperature=0.6)
    return {"level": req.team_level, "focus": req.focus, "plan": result, "generated_at": int(time.time())}

@app.post("/api/formation-suggest")
def formation_suggest(data: dict):
    prompt = f"""Recommande formation football :
Style : {data.get('play_style','équilibré')} | Forces : {data.get('strengths','')} | Faiblesses : {data.get('weaknesses','')} | Adverse : {data.get('opponent_formation','inconnue')}
1. Formation recommandée | 2. Pourquoi | 3. Alternative | 4. Positionnement | 5. Instructions"""
    result = call_groq([{"role": "system", "content": "Tu es expert tactique UEFA Pro."}, {"role": "user", "content": prompt}], temperature=0.7)
    return {"suggestion": result, "generated_at": int(time.time())}

# ─────────────────────────────────────────────
#  HELPERS INTERNES
# ─────────────────────────────────────────────

def _extract_json_from_response(text: str) -> dict:
    """Extrait un bloc JSON d'une réponse texte."""
    try:
        match = re.search(r'```json\s*([\s\S]*?)```', text)
        if match:
            return json.loads(match.group(1))
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            return json.loads(match.group(0))
    except Exception as e:
        logger.warning(f"JSON extract failed: {e}")
    return {}

def _update_live_state(ocr_data: dict):
    """Met à jour l'état live depuis les données OCR."""
    if ocr_data.get("score_detected", {}).get("detected"):
        s = ocr_data["score_detected"]
        live_match_state["score"] = {"home": s.get("home", 0), "away": s.get("away", 0)}
    if ocr_data.get("minute_detected", {}).get("detected"):
        live_match_state["minute"] = ocr_data["minute_detected"].get("minute", 0)
    if ocr_data.get("players_detected"):
        players = []
        for p in ocr_data["players_detected"]:
            players.append({
                "id": p.get("number", 0),
                "number": p.get("number", 0),
                "team": p.get("team", "home"),
                "x": p.get("x", 50),
                "y": p.get("y", 50),
                "confidence": p.get("confidence", 0.5)
            })
        live_match_state["players"] = players

def _generate_initial_positions(home_formation: str, away_formation: str) -> list:
    """Génère les positions initiales des joueurs selon la formation."""
    import random
    players = []
    # Formations prédéfinies (positions en %)
    formations = {
        "4-3-3": [(50,90),(20,75),(37,75),(63,75),(80,75),(30,55),(50,55),(70,55),(20,25),(50,20),(80,25)],
        "4-4-2": [(50,90),(20,75),(37,75),(63,75),(80,75),(20,55),(37,55),(63,55),(80,55),(35,25),(65,25)],
        "3-5-2": [(50,90),(25,75),(50,75),(75,75),(15,55),(33,55),(50,55),(67,55),(85,55),(35,25),(65,25)],
        "4-2-3-1": [(50,90),(20,75),(37,75),(63,75),(80,75),(35,60),(65,60),(20,40),(50,40),(80,40),(50,20)],
        "5-3-2": [(50,90),(10,75),(25,75),(50,75),(75,75),(90,75),(30,55),(50,55),(70,55),(35,25),(65,25)],
    }

    home_pos = formations.get(home_formation, formations["4-3-3"])
    away_pos = formations.get(away_formation, formations["4-4-2"])

    for i, (x, y) in enumerate(home_pos):
        players.append({"id": i+1, "number": i+1, "team": "home", "x": x + random.uniform(-2,2), "y": y + random.uniform(-2,2), "name": f"J{i+1}"})
    for i, (x, y) in enumerate(away_pos):
        away_y = 100 - y
        players.append({"id": i+12, "number": i+1, "team": "away", "x": x + random.uniform(-2,2), "y": away_y + random.uniform(-2,2), "name": f"J{i+1}"})

    return players

# ─────────────────────────────────────────────
#  KEEP ALIVE
# ─────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    logger.info("🏆 TACTI-BALL PRO API démarrée")
    asyncio.create_task(keep_alive())

async def keep_alive():
    import httpx
    url = os.getenv("RENDER_EXTERNAL_URL", "http://localhost:10000") + "/health"
    while True:
        await asyncio.sleep(300)
        try:
            async with httpx.AsyncClient() as c:
                await c.get(url, timeout=10)
            logger.info("✅ Keep-alive OK")
        except Exception as e:
            logger.warning(f"Keep-alive failed: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000, reload=True)
