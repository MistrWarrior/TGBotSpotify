import os
import re
import base64
import time
import json
import unicodedata
from urllib.parse import quote, urlparse
import logging

import requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

# -------------------- LOGGING --------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s"
)
logger = logging.getLogger("spotify-telegram-bot")

# -------------------- ENV ------------------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")
SPOTIFY_PLAYLIST_ID = os.getenv("SPOTIFY_PLAYLIST_ID")
MARKET = os.getenv("SPOTIFY_MARKET", "MX")

START_TIME = time.time()

# -------------------- SPOTIFY AUTH ----------------
_access_cache = {"token": None, "exp": 0}

def get_access_token() -> str:
    if _access_cache["token"] and time.time() < _access_cache["exp"]:
        return _access_cache["token"]

    token_url = "https://accounts.spotify.com/api/token"
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {"Authorization": f"Basic {basic}"}
    data = {
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
    }
    r = requests.post(token_url, headers=headers, data=data, timeout=20)
    r.raise_for_status()
    payload = r.json()
    access = payload["access_token"]
    expires_in = payload.get("expires_in", 3600)
    _access_cache["token"] = access
    _access_cache["exp"] = time.time() + expires_in * 0.9
    return access

# -------------------- SPOTIFY HELPERS -------------
def add_to_playlist(track_id: str):
    access = get_access_token()
    url = f"https://api.spotify.com/v1/playlists/{SPOTIFY_PLAYLIST_ID}/tracks"
    body = {"uris": [f"spotify:track:{track_id}"]}
    r = requests.post(url, headers={"Authorization": f"Bearer {access}"}, json=body, timeout=20)
    r.raise_for_status()
    return r.json()

def track_in_playlist(track_id: str) -> bool:
    access = get_access_token()
    url = (
        f"https://api.spotify.com/v1/playlists/{SPOTIFY_PLAYLIST_ID}/tracks"
        f"?fields=items(track(id)),next&limit=100"
    )
    headers = {"Authorization": f"Bearer {access}"}
    while url:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        for it in data.get("items", []):
            tr = it.get("track") or {}
            if tr.get("id") == track_id:
                return True
        url = data.get("next")
    return False

def playlist_count() -> int:
    access = get_access_token()
    url = f"https://api.spotify.com/v1/playlists/{SPOTIFY_PLAYLIST_ID}/tracks?limit=1"
    r = requests.get(url, headers={"Authorization": f"Bearer {access}"}, timeout=20)
    r.raise_for_status()
    total = r.json().get("total", 0)
    return total

# -------------------- SEARCH UTILS ----------------
def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[\W_]+", " ", s, flags=re.UNICODE).strip().lower()
    return s

def parse_query(user_text: str):
    t = user_text.strip()
    m = re.split(r"\s[-–—]\s", t, maxsplit=1)
    if len(m) == 2:
        title, artists_raw = m[0], m[1]
    else:
        title, artists_raw = t, ""
        if "," in t or re.search(r"\s+y\s+", t, flags=re.IGNORECASE):
            chunks = re.split(r",|\s+y\s+", t, flags=re.IGNORECASE)
            if len(chunks) >= 2:
                title = chunks[0].strip()
                artists_raw = " ".join(chunks[1:]).strip()
    title = re.sub(r"[\[\]()]+", " ", title).strip()
    artists = []
    if artists_raw:
        for a in re.split(r",|\s+y\s+|/&| x ", artists_raw, flags=re.IGNORECASE):
            a = a.strip()
            if a:
                artists.append(a)
    return title, artists

def extract_track_id_from_url(text: str) -> str | None:
    m = re.search(r"spotify:track:([A-Za-z0-9]+)", text)
    if m:
        return m.group(1)
    if "open.spotify.com/track/" in text:
        try:
            from urllib.parse import urlparse
            u = urlparse(text)
            parts = u.path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] == "track":
                return parts[1]
        except Exception:
            return None
    return None

def search_track(user_text: str, market="MX"):
    link_id = extract_track_id_from_url(user_text)
    if link_id:
        access = get_access_token()
        url = f"https://api.spotify.com/v1/tracks/{link_id}?market={market}"
        r = requests.get(url, headers={"Authorization": f"Bearer {access}"}, timeout=20)
        if r.status_code == 200:
            return r.json()
        r.raise_for_status()
        return None

    title, artists = parse_query(user_text)
    access = get_access_token()

    def do_search(q):
        url = f"https://api.spotify.com/v1/search?q={quote(q)}&type=track&market={market}&limit=5"
        r = requests.get(url, headers={"Authorization": f"Bearer {access}"}, timeout=20)
        r.raise_for_status()
        return r.json().get("tracks", {}).get("items", [])

    items = []
    if artists:
        q = f'track:"{title}" ' + " ".join([f'artist:"{a}"' for a in artists])
        items = do_search(q)

    if not items:
        q = f'track:"{title}"'
        items = do_search(q)

    if not items:
        q = user_text
        items = do_search(q)

    if not items:
        return None

    title_norm = norm(title)
    artists_norm = {norm(a) for a in artists}
    best = None
    best_score = -1.0

    for tr in items:
        tr_title = norm(tr["name"])
        tr_artists = {norm(a["name"]) for a in tr["artists"]}

        title_match = 1.0 if tr_title == title_norm else (
            0.7 if title_norm and (title_norm in tr_title or tr_title in title_norm) else 0.0
        )
        artist_match = 0.0
        if artists_norm:
            inter = artists_norm & tr_artists
            if inter:
                artist_match = 0.8
        score = title_match + artist_match

        if score > best_score:
            best_score = score
            best = tr

    if best_score < 0.6:
        return None
    return best

# -------------------- TELEGRAM COMMANDS -----------
HELP_TEXT = (
    "🤖 *Comandos disponibles:*\n\n"
    "/ping - Verificar si el bot responde\n"
    "/status - Revisar conexión con Spotify\n"
    "/help - Mostrar esta ayuda\n\n"
    "👉 Para agregar una canción escribe su *nombre* o pega un *link* de Spotify.\n"
    "Ejemplos:\n"
    "• `La playera (Bandolera) - Zion y Lennox`\n"
    "• `https://open.spotify.com/track/...`\n"
)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 Pong!")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        get_access_token()
        total = playlist_count()
        up = int(time.time() - START_TIME)
        h, m = divmod(up, 3600)
        m, s = divmod(m, 60)
        await update.message.reply_text(
            f"✅ Spotify OK\n"
            f"🎵 Canciones en playlist: {total}\n"
            f"⏱️ Uptime: {h:02d}:{m:02d}:{s:02d}"
        )
    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", "?")
        await update.message.reply_text(f"❌ Spotify ERROR (HTTP {code})")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# -------------------- TEXT HANDLER ----------------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = (update.message.text or "").strip()
    if not query:
        return

    try:
        track = search_track(query, MARKET)
        if not track:
            await update.message.reply_text(
                "❌ No encontré un match claro.\n"
                "Prueba con *Título - Artista* o pega el *link* de la canción.",
                parse_mode="Markdown",
            )
            return

        track_id = track["id"]
        if track_in_playlist(track_id):
            url = track["external_urls"]["spotify"]
            await update.message.reply_text(f"⚠️ Ya estaba en la playlist:\n🔗 {url}")
            return

        add_to_playlist(track_id)

        track_name = track["name"]
        artists = ", ".join(a["name"] for a in track["artists"])
        url = track["external_urls"]["spotify"]

        caption = f"✅ *Agregada:* {track_name} — {artists}\n🔗 {url}"
        await update.message.reply_text(caption, parse_mode="Markdown")

    except requests.HTTPError as e:
        logger.exception("HTTPError")
        code = getattr(e.response, "status_code", "?")
        await update.message.reply_text(f"⚠️ Error HTTP con Spotify: {code}")
    except Exception as e:
        logger.exception("Error general")
        await update.message.reply_text(f"⚠️ Error: {e}")

# -------------------- MAIN ------------------------
def main():
    if not all([BOT_TOKEN, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, SPOTIFY_PLAYLIST_ID]):
        missing = [k for k, v in {
            "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
            "SPOTIFY_CLIENT_ID": CLIENT_ID,
            "SPOTIFY_CLIENT_SECRET": CLIENT_SECRET,
            "SPOTIFY_REFRESH_TOKEN": REFRESH_TOKEN,
            "SPOTIFY_PLAYLIST_ID": SPOTIFY_PLAYLIST_ID,
        }.items() if not v]
        raise RuntimeError(f"Faltan variables de entorno: {', '.join(missing)}")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot corriendo...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
