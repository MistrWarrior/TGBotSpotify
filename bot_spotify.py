# bot_spotify.py
# Telegram → Spotify playlist bot
# Requiere variables de entorno:
# TELEGRAM_BOT_TOKEN, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET,
# SPOTIFY_REFRESH_TOKEN, SPOTIFY_PLAYLIST_ID, SPOTIFY_MARKET (ej: MX)

import os
import re
import time
import logging
import requests
from urllib.parse import urlparse

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("spotify-telegram-bot")

# ====== ENV ======
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN", "").strip()
SPOTIFY_PLAYLIST_ID = os.getenv("SPOTIFY_PLAYLIST_ID", "").strip()
MARKET = os.getenv("SPOTIFY_MARKET", "MX").strip() or "MX"

# ====== SPOTIFY AUTH ======
_SPOTIFY_TOKEN_CACHE = {"access": None, "exp": 0}

def get_access_token() -> str:
    # Cache simple (55 mins)
    if _SPOTIFY_TOKEN_CACHE["access"] and time.time() < _SPOTIFY_TOKEN_CACHE["exp"]:
        return _SPOTIFY_TOKEN_CACHE["access"]
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": REFRESH_TOKEN,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    access = data["access_token"]
    # spotify suele dar 3600s; dejamos margen
    _SPOTIFY_TOKEN_CACHE["access"] = access
    _SPOTIFY_TOKEN_CACHE["exp"] = time.time() + 3300
    return access

# ====== UTIL ======
def extract_track_id_from_url(text: str) -> str | None:
    """
    Acepta:
      - https://open.spotify.com/track/<id>
      - spotify:track:<id>
    """
    text = text.strip()
    if "open.spotify.com/track/" in text:
        try:
            path = urlparse(text).path
            tid = path.split("/track/")[1].split("/")[0]
            return tid
        except Exception:
            return None
    m = re.match(r"spotify:track:([A-Za-z0-9]+)", text)
    return m.group(1) if m else None

_word_re = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+")

def tokenize(s: str) -> list[str]:
    return [w.lower() for w in _word_re.findall(s)]

def score_match(query: str, title: str, artists: list[str]) -> float:
    """
    Scoring muy simple:
      - tokens en común query↔title y query↔artists
      - penaliza si title contiene 'live|remix|karaoke|version' y query no
    """
    q = set(tokenize(query))
    t = set(tokenize(title))
    a = set()
    for x in artists:
        a |= set(tokenize(x))

    base = len(q & t) + 0.5 * len(q & a)

    # si la query contiene palabas dentro de () inclúyelas
    paren = re.findall(r"\(([^)]+)\)", query, flags=re.IGNORECASE)
    for p in paren:
        for tok in tokenize(p):
            if tok in t or tok in a:
                base += 0.25

    flags = {"live", "remix", "karaoke", "version", "acoustic"}
    q_has_flag = any(f in q for f in flags)
    if not q_has_flag and any(f in t for f in flags):
        base -= 0.75

    # pequeña bonificación si primer artista aparece explícito en query
    if tokenize(artists[0]) and (set(tokenize(artists[0])) & q):
        base += 0.3

    return base

# ====== SPOTIFY OPS ======
def search_track(q: str, market: str = "MX") -> dict | None:
    """
    Si recibe link/URI devuelve el track directo.
    Si recibe texto, busca top 5 y escoge el mejor por score con umbral.
    """
    tid = extract_track_id_from_url(q)
    access = get_access_token()

    if tid:
        r = requests.get(
            f"https://api.spotify.com/v1/tracks/{tid}",
            headers={"Authorization": f"Bearer {access}"},
            params={"market": market},
            timeout=20,
        )
        if r.status_code == 200:
            return r.json()
        r.raise_for_status()

    r = requests.get(
        "https://api.spotify.com/v1/search",
        headers={"Authorization": f"Bearer {access}"},
        params={"q": q, "type": "track", "limit": 5, "market": market},
        timeout=20,
    )
    r.raise_for_status()
    items = r.json().get("tracks", {}).get("items", [])
    if not items:
        return None

    # elegir por score y umbral mínimo para evitar falsos positivos
    best = None
    best_score = -1e9
    for it in items:
        title = it["name"]
        artists = [a["name"] for a in it["artists"]]
        s = score_match(q, title, artists)
        if s > best_score:
            best = it
            best_score = s

    # umbral: al menos 1 token relevante en común
    if best and best_score >= 1.0:
        return best
    return None

def track_in_playlist(track_id: str) -> bool:
    access = get_access_token()
    url = f"https://api.spotify.com/v1/playlists/{SPOTIFY_PLAYLIST_ID}/tracks"
    params = {"market": MARKET, "fields": "items(track(id)),next", "limit": 100}
    while True:
        r = requests.get(url, headers={"Authorization": f"Bearer {access}"}, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        for it in data.get("items", []):
            if it.get("track", {}).get("id") == track_id:
                return True
        if data.get("next"):
            url = data["next"]
            params = None  # next ya lleva querystring
        else:
            break
    return False

def add_to_playlist(track_id: str):
    access = get_access_token()
    url = f"https://api.spotify.com/v1/playlists/{SPOTIFY_PLAYLIST_ID}/tracks"
    r = requests.post(url, headers={"Authorization": f"Bearer {access}"}, json={"uris": [f"spotify:track:{track_id}"]}, timeout=20)
    r.raise_for_status()
    return r.json()

def remove_from_playlist(track_id: str):
    access = get_access_token()
    url = f"https://api.spotify.com/v1/playlists/{SPOTIFY_PLAYLIST_ID}/tracks"
    body = {"tracks": [{"uri": f"spotify:track:{track_id}"}]}
    r = requests.delete(url, headers={"Authorization": f"Bearer {access}"}, json=body, timeout=20)
    r.raise_for_status()
    return r.json()

# ====== BOT TEXTOS ======
HELP = (
    "🤖 *Comandos disponibles:*\n\n"
    "/ping - Verificar si el bot responde\n"
    "/status - Revisar conexión con Spotify\n"
    "/help - Mostrar esta ayuda\n"
    "/remove <canción o link> - Eliminar canción de la playlist\n\n"
    "👉 Para *agregar* una canción escribe el *título* (y opcional el artista),\n"
    "   o pega un *link* de Spotify."
)

# ====== HANDLERS ======
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP, parse_mode="Markdown")

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 Pong! (bot OK)")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Solo prueba el refresh y que se pueda leer la playlist
        access = get_access_token()
        r = requests.get(
            f"https://api.spotify.com/v1/playlists/{SPOTIFY_PLAYLIST_ID}",
            headers={"Authorization": f"Bearer {access}"},
            params={"fields": "name,tracks.total"},
            timeout=20,
        )
        r.raise_for_status()
        info = r.json()
        await update.message.reply_text(f"✅ Conectado a Spotify\nPlaylist: *{info.get('name','?')}*\nTracks: {info.get('tracks',{}).get('total','?')}", parse_mode="Markdown")
    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", "?")
        await update.message.reply_text(f"⚠️ Error HTTP con Spotify: {code}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")

async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = " ".join(context.args).strip()
    if not q:
        await update.message.reply_text("❌ Usa `/remove <nombre o link>`", parse_mode="Markdown")
        return
    try:
        tr = search_track(q, MARKET)
        if not tr:
            await update.message.reply_text("❌ No encontré esa canción en Spotify.")
            return
        tid = tr["id"]
        if not track_in_playlist(tid):
            await update.message.reply_text("⚠️ Esa canción no está en la playlist.")
            return
        remove_from_playlist(tid)
        name = tr["name"]
        artists = ", ".join(a["name"] for a in tr["artists"])
        link = tr["external_urls"]["spotify"]
        await update.message.reply_text(f"🗑️ Eliminada: *{name}* — {artists}\n🔗 {link}", parse_mode="Markdown")
    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", "?")
        await update.message.reply_text(f"⚠️ Error HTTP con Spotify: {code}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        return
    try:
        tr = search_track(text, MARKET)
        if not tr:
            await update.message.reply_text("❌ No encontré esa canción. Intenta con *Título - Artista* o pega el *link* de Spotify.", parse_mode="Markdown")
            return

        tid = tr["id"]
        name = tr["name"]
        artists = ", ".join(a["name"] for a in tr["artists"])
        link = tr["external_urls"]["spotify"]

        if track_in_playlist(tid):
            await update.message.reply_text(f"🔁 Ya estaba en la playlist: *{name}* — {artists}\n🔗 {link}", parse_mode="Markdown")
            return

        add_to_playlist(tid)
        await update.message.reply_text(f"✅ Agregada: *{name}* — {artists}\n🔗 {link}", parse_mode="Markdown")

    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", "?")
        log.error("HTTPError", exc_info=True)
        await update.message.reply_text(f"⚠️ Error HTTP con Spotify: {code}")
    except Exception as e:
        log.exception("Error general")
        await update.message.reply_text(f"⚠️ Error: {e}")

def main():
    required = [
        ("TELEGRAM_BOT_TOKEN", BOT_TOKEN),
        ("SPOTIFY_CLIENT_ID", CLIENT_ID),
        ("SPOTIFY_CLIENT_SECRET", CLIENT_SECRET),
        ("SPOTIFY_REFRESH_TOKEN", REFRESH_TOKEN),
        ("SPOTIFY_PLAYLIST_ID", SPOTIFY_PLAYLIST_ID),
    ]
    missing = [k for k, v in required if not v]
    if missing:
        raise RuntimeError(f"Faltan variables de entorno: {', '.join(missing)}")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    log.info("Bot corriendo… ve a Telegram y prueba tu bot.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
