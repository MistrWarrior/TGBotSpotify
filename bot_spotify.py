import os
import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# =====================
# CONFIGURACIÓN LOGS
# =====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =====================
# VARIABLES DE ENTORNO
# =====================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")
SPOTIFY_PLAYLIST_ID = os.getenv("SPOTIFY_PLAYLIST_ID")
MARKET = os.getenv("SPOTIFY_MARKET", "MX")

# =====================
# FUNCIONES SPOTIFY
# =====================
def get_access_token():
    url = "https://accounts.spotify.com/api/token"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": SPOTIFY_REFRESH_TOKEN,
        "client_id": SPOTIFY_CLIENT_ID,
        "client_secret": SPOTIFY_CLIENT_SECRET,
    }
    r = requests.post(url, data=data)
    r.raise_for_status()
    return r.json()["access_token"]

def search_track(query, market="MX"):
    token = get_access_token()
    url = f"https://api.spotify.com/v1/search?q={query}&type=track&market={market}&limit=1"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    results = r.json()
    tracks = results.get("tracks", {}).get("items", [])
    if tracks:
        return tracks[0]
    return None

def add_to_playlist(track_id):
    token = get_access_token()
    url = f"https://api.spotify.com/v1/playlists/{SPOTIFY_PLAYLIST_ID}/tracks"
    r = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json={"uris": [f"spotify:track:{track_id}"]})
    r.raise_for_status()
    return r.json()

# =====================
# HANDLERS DE TELEGRAM
# =====================

# /start y /ping
async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 El bot está activo y listo 🚀")

# /status
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        _ = get_access_token()
        await update.message.reply_text("✅ Bot activo\n✅ Conexión a Spotify OK")
    except Exception as e:
        await update.message.reply_text(f"❌ Bot activo pero error con Spotify:\n{e}")

# /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *Comandos disponibles:*\n\n"
        "/ping - Verificar si el bot responde\n"
        "/status - Revisar conexión con Spotify\n"
        "/help - Mostrar esta ayuda\n\n"
        "👉 Para agregar una canción solo escribe su nombre.\n"
        "Ejemplo: `Morat Besos en Guerra`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# Manejar mensajes normales (buscar canción)
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    try:
        track = search_track(query, MARKET)
        if track:
            track_name = track["name"]
            artists = ", ".join([a["name"] for a in track["artists"]])
            url = track["external_urls"]["spotify"]
            add_to_playlist(track["id"])
            await update.message.reply_text(f"✅ Agregada: *{track_name}* — {artists}\n🔗 {url}", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ No encontré nada con: {query}")
    except Exception as e:
        logger.error("Error al procesar canción", exc_info=True)
        await update.message.reply_text(f"⚠️ Error: {e}")

# =====================
# MAIN
# =====================
def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Comandos
    application.add_handler(CommandHandler("start", ping))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("help", help_command))

    # Texto normal
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    application.run_polling()

if __name__ == "__main__":
    main()
