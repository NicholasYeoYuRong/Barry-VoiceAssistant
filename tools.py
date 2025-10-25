from livekit.agents import function_tool, RunContext
import requests
from langchain_community.tools import DuckDuckGoSearchRun
import asyncio, logging, os, time, json
import httpx
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

load_dotenv()

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE_URL = "https://api.spotify.com/v1"

_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")


OM_CODES = {
    0: "Clear sky",
    1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Heavy freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Heavy freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow",
    77: "Snow grains",
    80: "Light rain showers", 81: "Rain showers", 82: "Heavy rain showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ slight hail", 99: "Thunderstorm w/ heavy hail",
}

async def _geocode_city(client: httpx.AsyncClient, city: str) -> Optional[dict]:
    r = await client.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1, "language": "en", "format": "json"},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json() or {}
    results = data.get("results") or []
    return results[0] if results else None

async def _fetch_current_weather(client: httpx.AsyncClient, lat: float, lon: float, units: str) -> dict:
    # units: "metric" (C, km/h) or "imperial" (F, mph)
    temp_param   = "temperature_2m"
    feels_param  = "apparent_temperature"
    humid_param  = "relative_humidity_2m"
    wind_param   = "wind_speed_10m"
    precip_param = "precipitation"
    fields = ",".join([temp_param, feels_param, humid_param, wind_param, "weather_code", precip_param])

    use_f = units.lower().startswith("i")
    r = await client.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": fields,
            "temperature_unit": "fahrenheit" if use_f else "celsius",
            "wind_speed_unit": "mph" if use_f else "kmh",
            "precipitation_unit": "inch" if use_f else "mm",
            "timezone": "auto",
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json() or {}

def _fmt_current(city_label: str, payload: dict, units: str) -> str:
    cur = (payload.get("current") or {})
    tz  = payload.get("timezone", "local")
    t   = cur.get("time")
    w   = int(cur.get("weather_code")) if cur.get("weather_code") is not None else None
    desc = OM_CODES.get(w, "Unknown")
    temp = cur.get("temperature_2m")
    feels = cur.get("apparent_temperature")
    humid = cur.get("relative_humidity_2m")
    wind  = cur.get("wind_speed_10m")
    precip = cur.get("precipitation")

    deg = "°F" if units.lower().startswith("i") else "°C"
    wsp = "mph" if units.lower().startswith("i") else "km/h"
    ppu = "in" if units.lower().startswith("i") else "mm"

    # Build a single, concise line (great for realtime TTS agents)
    parts = [
        f"{city_label}: {desc}",
        f"{temp}{deg} (feels {feels}{deg})" if temp is not None and feels is not None else None,
        f"humidity {humid}%" if humid is not None else None,
        f"wind {wind} {wsp}" if wind is not None else None,
        f"precip {precip} {ppu}" if precip is not None else None,
        f"as of {t} {tz}" if t else None,
    ]
    return " | ".join(p for p in parts if p)


@function_tool()
async def get_weather(
    context: RunContext,
    city: str,
    units: str = "metric",   # "metric" or "imperial"
) -> str:
    """
    Up-to-date weather for a city, using Open-Meteo (no API key).
    Returns a concise one-liner with conditions, temp, humidity, wind, precip, and local time.
    """
    try:
        async with httpx.AsyncClient() as client:
            place = await _geocode_city(client, city)
            if not place:
                return f"Sorry, I couldn’t find “{city}”."

            lat, lon = place["latitude"], place["longitude"]
            label = f"{place.get('name')}, {place.get('country_code', '')}".strip().strip(",")
            data = await _fetch_current_weather(client, lat, lon, units)
            if not data.get("current"):
                return f"Weather is unavailable for {label} right now."

            return _fmt_current(label, data, units)
    except Exception as e:
        logging.exception("get_weather failed")
        return f"An error occurred while fetching the weather for {city}."

@function_tool()
async def web_search(
    context: RunContext,
    query: str,
    freshness_days: int = 3,
    max_results: int = 5,
) -> str:
    """
    Perform a web search using DuckDuckGo and return the top result.
    """
    try:

        tavily_key = os.getenv("TAVILY_API_KEY")
        if tavily_key:
            from tavily import TavilyClient
            client = TavilyClient(api_key=tavily_key)

            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: client.search(
                    query=query,
                    max_results=max_results,
                    search_depth="basic",
                    include_answer=True,
                    include_raw_content=False,
                    days=freshness_days,
                )
            )

            parts = []
            if result.get("answer"):
                parts.append(f"Answer: {result['answer']}")
            for r in result.get("results", [])[:max_results]:
                title = r.get("title", "Result")
                url = r.get("url", "")
                date = r.get("published_date") or r.get("date") or ""
                snippet = (r.get("content") or r.get("snippet") or "")[:240]
                line = f"- {title} — {url}" + (f" ({date})" if date else "")
                if snippet:
                    line += f"\n  {snippet}"
                parts.append(line)
            return "\n".join(parts) if parts else "No recent results found."
        
        # Fallback to DuckDuckGoSearchRun if Tavily API key is not set
        else:
            from duckduckgo_search import DDGS

            # map freshness to DDG timelimit
            if freshness_days <= 1:
                timelimit = "d"
            elif freshness_days <= 7:
                timelimit = "w"
            elif freshness_days <= 30:
                timelimit = "m"
            else:
                timelimit = None  # no time filter

            def _ddg_search():
                with DDGS() as ddgs:
                    return list(ddgs.text(
                        query,
                        max_results=max_results,
                        region="sg-en",            # your locale
                        safesearch="moderate",
                        timelimit=timelimit,       # ← recency control
                    ))

            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(None, _ddg_search)

            if not results:
                return "No recent results found."

            lines = []
            for r in results:
                title = r.get("title", "Result")
                url = r.get("href") or r.get("url", "")
                snippet = (r.get("body") or r.get("snippet") or "")[:240]
                date = r.get("date") or ""
                line = f"- {title} — {url}" + (f" ({date})" if date else "")
                if snippet:
                    line += f"\n  {snippet}"
                lines.append(line)
            return "\n".join(lines)
        
    except Exception as e:
        logging.error(f"Error during web search: {e}")
        return "An error occurred while performing the web search."




# SPOTIFY TOOLS

# Simple token cache in memory
_token_cache: Dict[str, Any] = {
    "access_token": None,
    "exp": 0,  # epoch time
}

def _load_token_cache() -> Dict[str, Any]:
    """Load token cache from file."""
    try:
        cache_file = "spotify_token_cache.json"
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                return json.load(f)
    except:
        pass
    return {"access_token": None, "exp": 0}

def _save_token_cache(cache: Dict[str, Any]):
    """Save token cache to file."""
    try:
        cache_file = "spotify_token_cache.json"
        with open(cache_file, 'w') as f:
            json.dump(cache, f)
    except Exception as e:
        logging.warning(f"Could not save token cache: {e}")

# Initialize token cache from file
_token_cache = _load_token_cache()

def _match_name(dev, name: str) -> bool:
    return name.lower() in (dev.get("name") or "").lower()

async def _get_device_id(require_active: bool = False, preferred_name: Optional[str] = None) -> str:
    """Get device ID with consistent logic."""
    devices = await _get_devices()
    if not devices:
        raise RuntimeError("No Spotify devices found. Open Spotify on any device and try again.")
    
    # 1. Preferred named device
    if preferred_name:
        for d in devices:
            if _match_name(d, preferred_name) and (not require_active or d.get("is_active")):
                return d["id"]
    
    # 2. Active device
    for d in devices:
        if d.get("is_active"):
            return d["id"]
    
    # 3. First device (if not requiring active)
    if not require_active:
        return devices[0]["id"]
    
    raise RuntimeError("No active device. Open Spotify and press play once, then try again.")

_token_refresh_lock = asyncio.Lock()

async def _get_spotify_token() -> str:
    async with _token_refresh_lock:
        now = time.time()
        if _token_cache["access_token"] and now < _token_cache["exp"] - 30:
            return _token_cache["access_token"]
    if not (_CLIENT_ID and _CLIENT_SECRET and _REFRESH_TOKEN):
        raise RuntimeError("Missing SPOTIFY_CLIENT_ID/SECRET/REFRESH_TOKEN.")
    async with httpx.AsyncClient(timeout=15) as client:
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
        if r.status_code != 200:
            raise RuntimeError(f"Token refresh failed: {r.status_code} {r.text[:300]}")
        data = r.json()
        access_token = data.get("access_token")
        if not access_token:
            raise RuntimeError(f"Token refresh returned no access_token: {data}")
        _token_cache["access_token"] = access_token
        _token_cache["exp"] = now + int(data.get("expires_in", 3600))
        _save_token_cache(_token_cache)  # Save to persistent storage
        return access_token
        
async def _api(method: str, path: str, *, params=None, json_body=None, retry=True) -> httpx.Response:
    token = await _get_spotify_token()  # raises if bad
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{SPOTIFY_API_BASE_URL}{path}"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.request(method, url, params=params, json=json_body, headers=headers)
        if r.status_code == 401 and retry:
            _token_cache["access_token"] = None
            token = await _get_spotify_token()
            headers["Authorization"] = f"Bearer {token}"
            r = await client.request(method, url, params=params, json=json_body, headers=headers)
        if r.status_code == 429 and retry:
            await asyncio.sleep(int(r.headers.get("Retry-After", "1")))
            return await _api(method, path, params=params, json_body=json_body, retry=False)
        return r
    

async def _search_track(query: str, limit=5) -> List[Dict[str, Any]]:
    r = await _api("GET", "/search", params={
        "q": query, "type": "track", "limit": limit, "market": "from_token"
    })
    if r.status_code != 200:
        raise RuntimeError(f"Search failed: {r.status_code} {r.text}")
    return (r.json().get("tracks") or {}).get("items", [])

async def _search_playlist(query: str, limit=5) -> List[Dict[str, Any]]:
    """Search for playlists by name."""
    r = await _api("GET", "/search", params={
        "q": query, "type": "playlist", "limit": limit, "market": "from_token"
    })
    if r.status_code != 200:
        raise RuntimeError(f"Playlist search failed: {r.status_code} {r.text}")
    return (r.json().get("playlists") or {}).get("items", [])

async def _search_artist(query: str, limit=5) -> List[Dict[str, Any]]:
    """Search for artists by name."""
    r = await _api("GET", "/search", params={
        "q": query, "type": "artist", "limit": limit, "market": "from_token"
    })
    if r.status_code != 200:
        raise RuntimeError(f"Artist search failed: {r.status_code} {r.text}")
    return (r.json().get("artists") or {}).get("items", [])

async def _queue_track_by_name(track_name: str, device_id: str) -> str:
    """Queue a track by searching for its name."""
    items = await _search_track(track_name, limit=1)
    if not items:
        raise RuntimeError(f"No track found with name: {track_name}")
    
    track_uri = items[0]["uri"]
    await _queue(track_uri, device_id)
    
    artist = ", ".join(a["name"] for a in items[0]["artists"])
    return f"Queued: {items[0]['name']} — {artist}"

async def _get_devices() -> List[Dict[str, Any]]:
    r = await _api("GET", "/me/player/devices")
    if r.status_code != 200:
        raise RuntimeError(f"Devices failed: {r.status_code} {r.text}")
    return r.json().get("devices", [])

async def _start_playback(uris: List[str], device_id: Optional[str] = None, position_ms: int = 0):
    body: Dict[str, Any] = {"uris": uris, "position_ms": position_ms}
    params = {"device_id": device_id} if device_id else None
    r = await _api("PUT", "/me/player/play", params=params, json_body=body)
    if r.status_code not in (200, 202, 204):
        raise RuntimeError(f"Start playback failed: {r.status_code} {r.text}")

async def _pause(device_id: Optional[str] = None):
    params = {"device_id": device_id} if device_id else None
    r = await _api("PUT", "/me/player/pause", params=params)
    if r.status_code not in (200, 202, 204):
        raise RuntimeError(f"Pause failed: {r.status_code} {r.text}")

async def _resume(device_id: Optional[str] = None):
    params = {"device_id": device_id} if device_id else None
    r = await _api("PUT", "/me/player/play", params=params)
    if r.status_code not in (200, 202, 204):
        raise RuntimeError(f"Resume failed: {r.status_code} {r.text}")

async def _next(device_id: Optional[str] = None):
    params = {"device_id": device_id} if device_id else None
    r = await _api("POST", "/me/player/next", params=params)
    if r.status_code not in (200, 202, 204):
        raise RuntimeError(f"Next failed: {r.status_code} {r.text}")

async def _previous(device_id: Optional[str] = None):
    params = {"device_id": device_id} if device_id else None
    r = await _api("POST", "/me/player/previous", params=params)
    if r.status_code not in (200, 202, 204):
        raise RuntimeError(f"Previous failed: {r.status_code} {r.text}")

async def _set_volume(volume_percent: int, device_id: Optional[str] = None):
    volume_percent = max(0, min(100, int(volume_percent)))
    params = {"volume_percent": volume_percent}
    if device_id:
        params["device_id"] = device_id
    r = await _api("PUT", "/me/player/volume", params=params)
    if r.status_code not in (200, 202, 204):
        raise RuntimeError(f"Volume failed: {r.status_code} {r.text}")

async def _queue(uri: str, device_id: Optional[str] = None):
    params = {"uri": uri}
    if device_id:
        params["device_id"] = device_id
    r = await _api("POST", "/me/player/queue", params=params)
    if r.status_code not in (200, 202, 204):
        raise RuntimeError(f"Queue failed: {r.status_code} {r.text}")

    
# Spotify API interaction
@function_tool(
    name="spotify_control",
    description="""Control Spotify playback. Available actions:
    - play_song: Play a song by name/artist (use 'query' parameter)
    - play_playlist: Play a playlist by name (use 'playlist_name' or 'query')
    - play_artist: Play an artist's music (use 'query')
    - queue_song: Add a song to queue by name (use 'track_name' or 'query')
    - queue_multiple_songs: Queue multiple songs (use 'query' with comma-separated names)
    - pause: Pause playback
    - resume: Resume playback
    - next: Skip to next track
    - previous: Go to previous track
    - volume: Set volume (0-100)"""
)
async def spotify_control(context: RunContext, action: str,
                          query: Optional[str] = None,
                          uri: Optional[str] = None,
                          device_name: Optional[str] = None,
                          volume: Optional[int] = None,
                          playlist_name: Optional[str] = None,
                          track_name: Optional[str] = None) -> str:

    logging.info("spotify_control called with: action=%s, query=%s, uri=%s, device_name=%s, volume=%s",
                 action, query, uri, device_name, volume, playlist_name, track_name)

    try:
        if action == "play_song":
            if not query:
                return "Please provide a song/artist query."
            items = await _search_track(query, limit=5)
            if not items:
                return f"No results for '{query}'."
            track = items[0]
            device_id = await _get_device_id(require_active=False, preferred_name=device_name)
            await _start_playback([track["uri"]], device_id=device_id)
            artist = ", ".join(a["name"] for a in track["artists"])
            return f"Playing: {track['name']} — {artist}"
        
        elif action == "play_playlist":
            if not playlist_name and not query:
                return "Please provide a playlist name to search for."
            
            search_query = playlist_name or query
            playlists = await _search_playlist(search_query, limit=5)
            if not playlists:
                return f"No playlists found for '{search_query}'."
            
            playlist = playlists[0]
            device_id = await _get_device_id(require_active=False, preferred_name=device_name)
            
            # Play the playlist context
            r = await _api("PUT", "/me/player/play",
                          params={"device_id": device_id},
                          json_body={"context_uri": playlist["uri"]})
            if r.status_code not in (200, 202, 204):
                return f"Failed to play playlist: {r.status_code} {r.text}"
            
            return f"Playing playlist: {playlist['name']} ({playlist['tracks']['total']} tracks)"
        
        elif action == "play_artist":
            if not query:
                return "Please provide an artist name."
            
            artists = await _search_artist(query, limit=5)
            if not artists:
                return f"No artists found for '{query}'."
            
            artist = artists[0]
            device_id = await _get_device_id(require_active=False, preferred_name=device_name)
            
            # Play artist's top tracks or context
            r = await _api("PUT", "/me/player/play",
                          params={"device_id": device_id},
                          json_body={"context_uri": artist["uri"]})
            if r.status_code not in (200, 202, 204):
                return f"Failed to play artist: {r.status_code} {r.text}"
            
            return f"Playing artist: {artist['name']}"
        
        elif action == "queue_song":
            if not track_name and not query:
                return "Please provide a song name to queue."
            
            track_query = track_name or query
            device_id = await _get_device_id(require_active=True, preferred_name=device_name)
            result = await _queue_track_by_name(track_query, device_id)
            return result

        elif action == "queue_multiple_songs":
            if not query:
                return "Please provide song names separated by commas."
            
            device_id = await _get_device_id(require_active=True, preferred_name=device_name)
            song_names = [name.strip() for name in query.split(",")]
            results = []
            
            for song_name in song_names:
                if song_name:  # Skip empty names
                    try:
                        result = await _queue_track_by_name(song_name, device_id)
                        results.append(result)
                    except Exception as e:
                        results.append(f"Failed to queue '{song_name}': {str(e)}")
            
            return "\n".join(results)

        elif action == "pause":
            device_id = await _get_device_id(require_active=True, preferred_name=device_name)
            await _pause(device_id)
            return "Paused."

        elif action == "resume":
            device_id = await _get_device_id(require_active=False, preferred_name=device_name)
            await _resume(device_id)
            return "Resumed."

        elif action == "next":
            device_id = await _get_device_id(require_active=True, preferred_name=device_name)
            await _next(device_id)
            return "Skipped to next track."

        elif action == "previous":
            device_id = await _get_device_id(require_active=True, preferred_name=device_name)
            await _previous(device_id)
            return "Went to previous track."

        elif action == "volume":
            if volume is None:
                return "Please provide a volume level (0-100)."
            device_id = await _get_device_id(require_active=True, preferred_name=device_name)
            await _set_volume(volume, device_id)
            return f"Volume set to {volume}%."

        # elif action == "queue":
        #     if not uri or not uri.startswith("spotify:track:"):
        #         return "Queue only accepts track URIs (spotify:track:...)."
        #     device_id = await _get_device_id(require_active=True, preferred_name=device_name)
        #     await _queue(uri, device_id)
        #     return "Track added to queue."

        else:
            return f"Unknown action: {action}"

    except RuntimeError as e:
        logging.error(f"Spotify control error: {e}")
        return str(e)
    except Exception as e:
        logging.exception("Unexpected error in spotify_control")
        return f"Spotify action failed: {str(e)}"