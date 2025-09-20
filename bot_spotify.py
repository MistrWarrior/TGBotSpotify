import os
import logging
from urllib.parse import quote
import requests
from base64 import b64encode
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# Cargar variables desde .env
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("spotify-telegram-bot")

# ----- Configuración -----
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")
PLAYLIST_ID = os.getenv("SPOTIFY_PLAYLIST_ID")
MARKET = os.getenv("SPOTIFY_MARKET", "MX")

def get_access_token() -> str:
    """Genera un access token nuevo usando el refresh token."""
    token_url = "https://accounts.spotify.com/api/token"
    auth_header = b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    data = {"grant_type": "refresh_token", "refresh_token": REFRESH_TOKEN}
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    r = requests.post(token_url, data=data, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]

def search_track(q: str, market: str = "MX"):
    """Busca una canción en Spotify y devuelve la primera coincidencia."""
    access = get_access_token()
    url = f"https://api.spotify.com/v1/search?q={quote(q)}&type=track&limit=5&market={market}"
    r = requests.get(url, headers={"Authorization": f"Bearer {access}"}, timeout=15)
    r.raise_for_status()
    items = r.json().get("tracks", {}).get("items", [])
    return items[0] if items else None

def add_to_playlist(track_uri: str):
    """Agrega una canción a la playlist configurada en .env."""
    access = get_access_token()
    url = f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks"
    r = requests.post(url, json={"uris": [track_uri]}, headers={"Authorization": f"Bearer {access}"}, timeout=15)
    r.raise_for_status()
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🎧 Envíame el *nombre de una canción* (puedes incluir artista) y la agrego a la playlist.\n\n"
        "Ejemplos:\n"
        "• Someone Like You Adele\n"
        "• Tusa Karol G\n\n"
        f"Playlist objetivo: `{PLAYLIST_ID}`"
    )
    await update.message.reply_markdown(msg)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = (update.message.text or "").strip()
    if not q:
        await update.message.reply_text("Mándame el nombre de la canción 😉")
        return

    try:
        track = search_track(q, MARKET)
        if not track:
            await update.message.reply_text("❌ No encontré resultados. Intenta con 'canción + artista'.")
            return

        track_uri = track["uri"]
        name = track["name"]
        artists = ", ".join([a["name"] for a in track["artists"]])
        url = track["external_urls"]["spotify"]

        add_to_playlist(track_uri)

        await update.message.reply_markdown(f"✅ Agregada: *{name}* — {artists}\n🔗 {url}")

    except requests.HTTPError as e:
        logger.exception("HTTPError")
        code = getattr(e.response, "status_code", "?")
        await update.message.reply_text(f"⚠️ Error HTTP con Spotify: {code}")
    except Exception as e:
        logger.exception("Error inesperado")
        await update.message.reply_text("⚠️ Ocurrió un error inesperado. Intenta otra vez.")

def main():
    if not all([BOT_TOKEN, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, PLAYLIST_ID]):
        raise SystemExit("❌ Faltan variables en .env")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("🤖 Bot corriendo... ve a Telegram y prueba tu bot.")
    app.run_polling()

if __name__ == "__main__":
    main()
