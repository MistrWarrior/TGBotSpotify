import os, re, json, logging, unicodedata
from difflib import SequenceMatcher
from urllib.parse import urlparse
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("spotify-telegram-bot")

# === ENV ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")
PLAYLIST_ID = os.getenv("SPOTIFY_PLAYLIST_ID")
MARKET = os.getenv("SPOTIFY_MARKET", "MX")

# === Helpers de normalización y similitud ===
def strip_accents(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')

def normalize(s: str) -> str:
    s = strip_accents(s.lower())
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def sim(a: str, b: str) -> float:
    a_n, b_n = normalize(a), normalize(b)
    if not a_n or not b_n:
        return 0.0
    # un pequeño boost si uno contiene al otro
    contain_boost = 0.1 if (a_n in b_n or b_n in a_n) else 0.0
    return min(1.0, SequenceMatcher(None, a_n, b_n).ratio() + contain_boost)

def fmt_track(t: dict) -> str:
    name = t.get("name")
    artists = ", ".join(a["name"] for a in t.get("artists", []))
    return f"{name} — {artists}"

def extract_track_id_from_url(text: str) -> str | None:
    m = re.search(r'open\.spotify\.com/track/([A-Za-z0-9]+)', text)
    if m: return m.group(1)
    m = re.search(r'spotify:track:([A-Za-z0-9]+)', text)
    if m: return m.group(1)
    return None

# === Spotify API básicas ===
def get_access_token() -> str:
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "refresh_token", "refresh_token": REFRESH_TOKEN},
        auth=(CLIENT_ID, CLIENT_SECRET),
        timeout=20,
    )
    r.raise_for_status()
    return r.json()["access_token"]

def search_tracks(q: str, market: str, limit: int = 10) -> list[dict]:
    access = get_access_token()
    r = requests.get(
        "https://api.spotify.com/v1/search",
        headers={"Authorization": f"Bearer {access}"},
        params={"q": q, "type": "track", "limit": limit, "market": market},
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("tracks", {}).get("items", [])

def get_playlist_items(limit: int = 100) -> list[dict]:
    access = get_access_token()
    items = []
    url = f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks"
    params = {"limit": min(limit, 100), "market": MARKET}
    while url and len(items) < limit:
        r = requests.get(url, headers={"Authorization": f"Bearer {access}"}, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        for it in data.get("items", []):
            if it.get("track"):
                items.append(it["track"])
        url = data.get("next")
        params = None  # 'next' ya incluye query
    return items

def add_track_to_playlist(track_id: str) -> None:
    access = get_access_token()
    uri = f"spotify:track:{track_id}"
    r = requests.post(
        f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks",
        headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"},
        data=json.dumps({"uris": [uri]}),
        timeout=20,
    )
    r.raise_for_status()

def remove_track_from_playlist_by_uri(uri: str) -> None:
    access = get_access_token()
    payload = {"tracks": [{"uri": uri}]}
    r = requests.delete(
        f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks",
        headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=20,
    )
    r.raise_for_status()

# === Lógica de selección flexible ===
def best_search_candidate(query: str, market: str) -> dict | None:
    results = search_tracks(query, market, limit=10)
    if not results:
        return None
    Scored = []
    for t in results:
        title = t["name"]
        artists = ", ".join(a["name"] for a in t["artists"])
        label = f"{title} — {artists}"
        Scored.append((sim(query, label), t))
    Scored.sort(key=lambda x: x[0], reverse=True)
    score, track = Scored[0]
    # Aceptamos si la similitud es decente; si no, igual regresamos para decidir luego
    return track

def find_in_playlist_by_id(track_id: str, playlist: list[dict]) -> dict | None:
    for t in playlist:
        if t.get("id") == track_id:
            return t
    return None

def best_playlist_match(query: str, playlist: list[dict]) -> tuple[dict | None, float]:
    best_t, best_s = None, 0.0
    qn = normalize(query)
    for t in playlist:
        label = fmt_track(t)
        s = sim(qn, label)
        if s > best_s:
            best_s, best_t = s, t
    return best_t, best_s

# === Telegram Handlers ===
async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await cmd_help(update, _)

async def cmd_help(update: Update, _: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *Comandos disponibles:*\n\n"
        "/ping — Verificar si el bot responde\n"
        "/status — Revisar acceso a Spotify y a la playlist\n"
        "/help — Mostrar esta ayuda\n"
        "/remove <texto|link> — Eliminar una canción de la playlist\n\n"
        "👉 Para *agregar* una canción solo escribe su nombre.\n"
        "   Ejemplo: _Morat Besos en Guerra_\n"
    )
    await update.message.reply_markdown(text)

async def cmd_ping(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 Pong!")

async def cmd_status(update: Update, _: ContextTypes.DEFAULT_TYPE):
    try:
        _ = get_access_token()
        pls = get_playlist_items(limit=1)
        ok = "✅" if isinstance(pls, list) else "⚠️"
        await update.message.reply_text(f"🧪 Spotify OK\nPlaylist ID: {PLAYLIST_ID}\nAcceso: ✅\nPlaylist: {ok}")
    except Exception as e:
        log.exception("STATUS error")
        await update.message.reply_text(f"❌ Error comprobando Spotify: {e}")

async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Uso: /remove <link|Título - Artista|título>")
        return

    query = " ".join(args).strip()
    try:
        # 1) Si viene link, eliminamos directo
        link_id = extract_track_id_from_url(query)
        playlist = get_playlist_items(limit=500)
        if link_id:
            t = find_in_playlist_by_id(link_id, playlist)
            if not t:
                await update.message.reply_text("⚠️ Esa canción no está en la playlist.")
                return
            uri = f"spotify:track:{link_id}"
            remove_track_from_playlist_by_uri(uri)
            await update.message.reply_text(f"🗑️ Eliminada: {fmt_track(t)}")
            return

        # 2) Si vino texto, buscamos candidato y validamos contra playlist
        candidate = best_search_candidate(query, MARKET)
        if candidate:
            cand_id = candidate["id"]
            t = find_in_playlist_by_id(cand_id, playlist)
            if t:
                remove_track_from_playlist_by_uri(f"spotify:track:{cand_id}")
                await update.message.reply_text(f"🗑️ Eliminada: {fmt_track(t)}")
                return

        # 3) Si no está la del buscador, probamos fuzzy dentro de la playlist
        best, score = best_playlist_match(query, playlist)
        if best and score >= 0.60:
            remove_track_from_playlist_by_uri(f"spotify:track:{best['id']}")
            await update.message.reply_text(f"🗑️ Eliminada (coincidencia ~{int(score*100)}%): {fmt_track(best)}")
            return

        # 4) Sugerencias (Top 3 similares) si nada claro
        suggestions = sorted(
            [(sim(query, fmt_track(t)), t) for t in playlist],
            key=lambda x: x[0], reverse=True
        )[:3]
        msg = "❌ No encontré una coincidencia clara para eliminar.\n"
        if suggestions and suggestions[0][0] > 0.35:
            msg += "Quizá te referías a:\n"
            for s, t in suggestions:
                msg += f"• {fmt_track(t)} (≈{int(s*100)}%)\n"
            msg += "\nPrueba con *Título - Artista* o pega el *link* de la canción."
        else:
            msg += "Prueba con *Título - Artista* o pega el *link* de la canción."
        await update.message.reply_markdown(msg)

    except requests.HTTPError as e:
        log.exception("HTTPError en /remove")
        await update.message.reply_text(f"❌ Error con Spotify: {e.response.status_code}")
    except Exception as e:
        log.exception("Error en /remove")
        await update.message.reply_text(f"❌ Error: {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Agregar canciones escribiendo el nombre o pegando el link
    text = update.message.text.strip()
    try:
        # Si pegó link, extraemos id directo
        track_id = extract_track_id_from_url(text)
        if not track_id:
            # Buscar mejor candidato por texto
            track = best_search_candidate(text, MARKET)
            if not track:
                await update.message.reply_text(
                    "❌ No encontré un match claro.\nPrueba con *Título - Artista* o pega el *link* de la canción.",
                    parse_mode="Markdown"
                )
                return
            track_id = track["id"]
            label = fmt_track(track)
        else:
            # Si traía link, armamos label con una consulta ligera para avisar bonito
            sr = search_tracks(f"track:{track_id}", MARKET, limit=1)
            label = fmt_track(sr[0]) if sr else "canción"

        add_track_to_playlist(track_id)
        url = f"https://open.spotify.com/track/{track_id}"
        await update.message.reply_text(f"✅ Agregada: {label}\n🔗 {url}")
    except requests.HTTPError as e:
        log.exception("HTTPError al agregar")
        await update.message.reply_text(f"❌ Error con Spotify: {e.response.status_code}")
    except Exception as e:
        log.exception("Error al agregar")
        await update.message.reply_text(f"❌ Error: {e}")

def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("remove", cmd_remove))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    log.info("Bot corriendo…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    # Si corres local, usa las env de tu .env (opcional si ya las tienes en Render)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    main()
