"""YouTube Music catalogue via ytmusicapi (unofficial, no sign-in).

Everything leaves this module as plain dicts:
  track  {kind:'track', id, title, artists:[{name,id}], album:{name,id}|None, duration, art}
"""

import os
import re

from ytmusicapi import YTMusic

# Countries offered in Settings (YouTube Music's charts and results differ by
# country). "auto" uses the device's locale, falling back to the US.
REGIONS = {
    "US": "United States", "GB": "United Kingdom", "CA": "Canada", "AU": "Australia", "IN": "India",
    "DE": "Germany", "FR": "France", "ES": "Spain", "IT": "Italy", "NL": "Netherlands", "SE": "Sweden",
    "PL": "Poland", "BR": "Brazil", "MX": "Mexico", "AR": "Argentina", "JP": "Japan", "KR": "South Korea",
    "ID": "Indonesia", "PH": "Philippines", "TR": "Türkiye", "ZA": "South Africa", "NG": "Nigeria",
    "AE": "United Arab Emirates", "SA": "Saudi Arabia",
}

_client = None
_region = "auto"


def auto_region():
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        match = re.match(r"^[a-z]{2,3}_([A-Z]{2})", os.environ.get(name, ""))
        if match and match.group(1) in REGIONS:
            return match.group(1)
    return "US"


def effective_region():
    return _region if _region in REGIONS else auto_region()


def set_region(region):
    """Switch country; the next request uses it."""
    global _client, _region
    _region = region if region in REGIONS else "auto"
    _client = None


def client():
    global _client
    if _client is None:
        _client = YTMusic(language="en", location=effective_region())
    return _client


def _art(thumbnails, size=544):
    if not thumbnails:
        return None
    best = max(thumbnails, key=lambda t: t.get("width") or 0)
    url = best.get("url")
    return re.sub(r"=w\d+-h\d+", f"=w{size}-h{size}", url) if url else None


def _seconds(text):
    if not text:
        return None
    total = 0
    for part in str(text).split(":"):
        if not part.isdigit():
            return None
        total = total * 60 + int(part)
    return total


def _track(item):
    video_id = item.get("videoId")
    if not video_id:
        return None
    album = item.get("album")
    return {
        "kind": "track",
        "id": video_id,
        "title": item.get("title") or "",
        "artists": [{"name": a.get("name"), "id": a.get("id")} for a in item.get("artists") or [] if a.get("name")],
        "album": {"name": album.get("name"), "id": album.get("id")} if isinstance(album, dict) and album.get("name") else None,
        "duration": item.get("duration_seconds") or _seconds(item.get("duration") or item.get("length")),
        "art": _art(item.get("thumbnails") or item.get("thumbnail")),
    }


def search(query):
    query = str(query or "").strip()[:200]
    if not query:
        return []
    results = client().search(query, filter="songs", limit=20)
    return [t for t in (_track(item) for item in results) if t][:20]


def radio(video_id):
    """YouTube Music's automatic 'up next' mix for a song."""
    data = client().get_watch_playlist(videoId=str(video_id), limit=50)
    return [t for t in (_track(item) for item in data.get("tracks") or []) if t]
