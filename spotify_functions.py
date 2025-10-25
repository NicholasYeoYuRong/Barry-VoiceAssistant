import os
import time
import logging
import httpx
from typing import Optional, Dict, Any, List

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE_URL = "https://api.spotify.com/v1"

_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")

# Simple token cache in memory
_token_cache = Dict[str, Any] = {
    "access_token": None,
    "exp": 0,  # epoch time
}

async def _get_spotify_token() -> Optional[str]:
    """
    Refresh the Spotify access token using the refresh token.
    """

    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["exp"] - 30:
        return _token_cache["access_token"]  # still valid
    
    if not (_CLIENT_ID and _CLIENT_SECRET and _REFRESH_TOKEN):
        logging.error("Spotify API credentials are not fully set in environment variables.")
        return None
    
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(
                SPOTIFY_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": _REFRESH_TOKEN,
                    "client_id": _CLIENT_ID,
                    "client_secret": _CLIENT_SECRET,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            r.raise_for_status()
            data = r.json()
            access_token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)  # default to 1 hour
            if access_token:
                _token_cache["access_token"] = access_token
                _token_cache["exp"] = now + int(expires_in)
                return access_token
            else:
                logging.error("Failed to obtain access token from Spotify response.")
                return None
        except Exception as e:
            logging.exception("Error refreshing Spotify token")
            return None
        
async def _api(
        method: str, path: str, *, params=None, json_body=None, retry=True
) -> httpx.Response:
    token = await _get_spotify_token()
    if not token:
        raise RuntimeError("No valid Spotify access token available.")
    
    url = f"{SPOTIFY_API_BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.request(method, url, headers=headers, params=params, json=json_body)
        if r.status_code == 401 and retry:
            # Token might have expired, try refreshing once
            _token_cache["access_token"] = None
            token = await _get_spotify_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
                r = await client.request(method, url, headers=headers, params=params, json=json_body)
        r.raise_for_status()
        return r
    
