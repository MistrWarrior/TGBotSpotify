import os
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

# Cargar variables desde .env
load_dotenv()

# Scopes que necesitamos para poder modificar playlists
SCOPE = "playlist-modify-public playlist-modify-private"

def main():
    # Crear objeto de autorización con Spotipy
    auth = SpotifyOAuth(
        scope=SCOPE,
        open_browser=True,
    )

    # Generar URL de autorización
    auth_url = auth.get_authorize_url()
    print("👉 Abre esta URL en tu navegador y autoriza el acceso:")
    print(auth_url)

    # Después de autorizar en el navegador, Spotify te redirigirá a example.com con un ?code=...
    # Copia esa URL completa de la barra del navegador y pégala aquí
    redirect_response = input("\nPega aquí la URL completa de redirección: ").strip()

    # Extraer el "code" de la URL
    code = auth.parse_response_code(redirect_response)
    token_info = auth.get_access_token(code, as_dict=True)

    # Mostrar el refresh token
    print("\n=== COPIA TU REFRESH TOKEN (guárdalo en .env) ===")
    print(token_info["refresh_token"])
    print("=========================================")

if __name__ == "__main__":
    main()
