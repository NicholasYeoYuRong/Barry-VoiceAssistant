import os, requests

cid  = os.getenv("SPOTIFY_CLIENT_ID")
sec  = os.getenv("SPOTIFY_CLIENT_SECRET")
ref  = os.getenv("SPOTIFY_REFRESH_TOKEN")

r = requests.post(
    "https://accounts.spotify.com/api/token",
    data={"grant_type": "refresh_token", "refresh_token": ref},
    auth=(cid, sec),
)

print(r.status_code)
print(r.text)