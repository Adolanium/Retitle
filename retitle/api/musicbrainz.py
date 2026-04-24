import dataclasses
import time

import requests


USER_AGENT = "Retitle/0.1.0 ( https://github.com/Adolanium/Retitle )"


@dataclasses.dataclass
class TrackInfo:
    position: int
    title: str
    length_ms: int | None = None


@dataclasses.dataclass
class ReleaseSearchResult:
    release_id: str
    title: str
    artist: str
    year: int | None
    country: str | None
    track_count: int | None
    score: int


@dataclasses.dataclass
class ReleaseDetails:
    release_id: str
    title: str
    artist: str
    year: int | None
    tracks: list[TrackInfo]


class MusicBrainzClient:
    BASE_URL = "https://musicbrainz.org/ws/2"

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })
        self._search_cache: dict[str, list[ReleaseSearchResult]] = {}
        self._release_cache: dict[str, ReleaseDetails] = {}
        self._last_request_time = 0.0

    def _throttle(self):
        """MusicBrainz allows ~1 req/sec anonymously."""
        elapsed = time.time() - self._last_request_time
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        self._last_request_time = time.time()

    def _get(self, endpoint: str, params: dict | None = None) -> requests.Response:
        self._throttle()
        url = f"{self.BASE_URL}{endpoint}"
        resp = self._session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp

    def search_release(
        self,
        album: str,
        artist: str | None = None,
        limit: int = 10,
    ) -> list[ReleaseSearchResult]:
        """Search for a release (album) by title and optional artist."""
        cache_key = f"{(artist or '').lower()}|{album.lower()}|{limit}"
        if cache_key in self._search_cache:
            return self._search_cache[cache_key]

        query_parts = [f'release:"{_escape(album)}"']
        if artist:
            query_parts.append(f'artist:"{_escape(artist)}"')
        query = " AND ".join(query_parts)

        resp = self._get(
            "/release", params={"query": query, "limit": limit, "fmt": "json"},
        )
        data = resp.json()

        results = []
        for r in data.get("releases", []):
            artist_credits = r.get("artist-credit", [])
            artist_name = "".join(
                (c.get("name") or c.get("artist", {}).get("name", "")) + c.get("joinphrase", "")
                for c in artist_credits
            ).strip() or "Unknown Artist"

            date = r.get("date", "")
            year = int(date[:4]) if date and len(date) >= 4 and date[:4].isdigit() else None
            track_count = r.get("track-count")
            if track_count is None:
                media = r.get("media", [])
                if media:
                    track_count = sum(m.get("track-count", 0) for m in media)

            results.append(ReleaseSearchResult(
                release_id=r["id"],
                title=r.get("title", ""),
                artist=artist_name,
                year=year,
                country=r.get("country"),
                track_count=track_count,
                score=int(r.get("score", 0)),
            ))

        self._search_cache[cache_key] = results
        return results

    def get_release(self, release_id: str) -> ReleaseDetails | None:
        """Fetch full release details including track list."""
        if release_id in self._release_cache:
            return self._release_cache[release_id]

        try:
            resp = self._get(
                f"/release/{release_id}",
                params={"inc": "recordings+artist-credits", "fmt": "json"},
            )
        except requests.HTTPError:
            return None

        data = resp.json()
        artist_credits = data.get("artist-credit", [])
        artist_name = "".join(
            (c.get("name") or c.get("artist", {}).get("name", "")) + c.get("joinphrase", "")
            for c in artist_credits
        ).strip() or "Unknown Artist"

        date = data.get("date", "")
        year = int(date[:4]) if date and len(date) >= 4 and date[:4].isdigit() else None

        tracks: list[TrackInfo] = []
        for medium in data.get("media", []):
            for t in medium.get("tracks", []):
                pos = t.get("position") or t.get("number")
                try:
                    pos_int = int(pos)
                except (TypeError, ValueError):
                    continue
                length = t.get("length")
                try:
                    length_ms = int(length) if length else None
                except (TypeError, ValueError):
                    length_ms = None
                title = t.get("title") or t.get("recording", {}).get("title", "")
                tracks.append(TrackInfo(
                    position=pos_int,
                    title=title,
                    length_ms=length_ms,
                ))

        tracks.sort(key=lambda t: t.position)
        details = ReleaseDetails(
            release_id=release_id,
            title=data.get("title", ""),
            artist=artist_name,
            year=year,
            tracks=tracks,
        )
        self._release_cache[release_id] = details
        return details


def _escape(term: str) -> str:
    """Escape Lucene query syntax for MusicBrainz."""
    specials = r'+-&|!(){}[]^"~*?:\\/'
    return "".join("\\" + c if c in specials else c for c in term)
