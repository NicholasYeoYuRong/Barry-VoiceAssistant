import base64, requests, urllib.parse, http.server, threading

CLIENT_ID = "6a596267a8bc484ca6fd4c569daa3b97"
CLIENT_SECRET = "909648398c0341c69960e5021358e378"
REDIRECT_URI = "http://127.0.0.1:3000/callback"  # ← make this match the Dashboard exactly
SCOPES = "user-modify-playback-state user-read-playback-state user-read-currently-playing user-read-private user-read-email streaming app-remote-control playlist-read-private playlist-read-collaborative playlist-modify-public playlist-modify-private"

print("Ensure this is added in your Spotify app Redirect URIs:\n  ", REDIRECT_URI, "\n")

auth_url = (
    "https://accounts.spotify.com/authorize?" +
    urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,   # must match exactly
        "scope": SCOPES,
    })
)
print("Open this URL in your browser:\n", auth_url, "\n")

code_holder = {}
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if urllib.parse.urlparse(self.path).path == urllib.parse.urlparse(REDIRECT_URI).path:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code_holder["code"] = qs.get("code", [None])[0]
            self.send_response(200); self.end_headers()
            self.wfile.write(b"You may close this window.")
    def log_message(self, *args): pass

host = urllib.parse.urlparse(REDIRECT_URI).hostname or "localhost"
port = int(urllib.parse.urlparse(REDIRECT_URI).port or 80)
server = http.server.HTTPServer((host, port), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
input("After authorizing in the browser, press Enter here…")
server.shutdown()

code = code_holder.get("code")
if not code:
    raise SystemExit("No authorization code received. Did the redirect URI match exactly?")

creds = f"{CLIENT_ID}:{CLIENT_SECRET}"
b64 = base64.b64encode(creds.encode()).decode()
r = requests.post(
    "https://accounts.spotify.com/api/token",
    data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI},
    headers={"Authorization": f"Basic {b64}"},
)
print("\nToken response:\n", r.json())