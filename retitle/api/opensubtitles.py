import dataclasses
import os
import struct
import time
from pathlib import Path

import requests
from dotenv import load_dotenv


def compute_hash(filepath: Path) -> tuple[str, int]:
    """Compute OpenSubtitles hash for a video file.

    Algorithm: sum 64-bit little-endian integers from first and last 64KB,
    add file size, mask to 64 bits, return as 16-char hex string.

    Returns (hash_hex, file_size_bytes).
    """
    BLOCK_SIZE = 65536  # 64KB

    filesize = filepath.stat().st_size
    if filesize < BLOCK_SIZE * 2:
        raise ValueError(f"File too small for hash computation: {filesize} bytes")

    hash_val = filesize

    with open(filepath, "rb") as f:
        # First 64KB
        buf = f.read(BLOCK_SIZE)
        longlongs = struct.unpack(f"<{len(buf) // 8}q", buf)
        hash_val += sum(longlongs)

        # Last 64KB
        f.seek(max(0, filesize - BLOCK_SIZE))
        buf = f.read(BLOCK_SIZE)
        longlongs = struct.unpack(f"<{len(buf) // 8}q", buf)
        hash_val += sum(longlongs)

    hash_val &= 0xFFFFFFFFFFFFFFFF
    return f"{hash_val:016x}", filesize


@dataclasses.dataclass
class SubtitleSearchResult:
    file_id: int
    language: str
    release: str
    download_count: int
    from_trusted: bool


@dataclasses.dataclass
class SubtitleDownloadResult:
    download_url: str
    file_name: str
    remaining: int


class OpenSubtitlesClient:
    BASE_URL = "https://api.opensubtitles.com/api/v1"

    def __init__(
        self,
        api_key: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ):
        load_dotenv()
        self.api_key = api_key or os.getenv("OPENSUBTITLES_API_KEY")
        self.username = username or os.getenv("OPENSUBTITLES_USERNAME")
        self.password = password or os.getenv("OPENSUBTITLES_PASSWORD")

        if not self.api_key or self.api_key == "your_api_key_here":
            raise ValueError(
                "OpenSubtitles API key not configured. "
                "Set OPENSUBTITLES_API_KEY in .env file or pass it directly."
            )

        self._session = requests.Session()
        self._session.headers.update({
            "Api-Key": self.api_key,
            "User-Agent": "Retitle v0.1.0",
            "Content-Type": "application/json",
        })
        self._last_request_time = 0.0
        self._token: str | None = None
        self._token_expiry: float = 0.0

        # Cache: (query_key) -> list[SubtitleSearchResult]
        self._search_cache: dict[str, list[SubtitleSearchResult]] = {}

    def _throttle(self):
        """Rate limiting: at least 0.2s between requests (5 req/sec max)."""
        elapsed = time.time() - self._last_request_time
        if elapsed < 0.2:
            time.sleep(0.2 - elapsed)
        self._last_request_time = time.time()

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        self._throttle()
        url = f"{self.BASE_URL}{endpoint}"
        resp = self._session.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _post(self, endpoint: str, json_data: dict | None = None) -> dict:
        self._throttle()
        url = f"{self.BASE_URL}{endpoint}"
        resp = self._session.post(url, json=json_data, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _ensure_authenticated(self):
        """Login if no valid token exists. Required for /download."""
        if self._token and time.time() < self._token_expiry:
            return

        if not self.username or not self.password:
            raise ValueError(
                "OpenSubtitles username/password not configured. "
                "Set OPENSUBTITLES_USERNAME and OPENSUBTITLES_PASSWORD in .env "
                "to enable subtitle downloads."
            )

        data = self._post("/login", json_data={
            "username": self.username,
            "password": self.password,
        })
        self._token = data["token"]
        # Token valid for 24h; refresh after 23h to be safe
        self._token_expiry = time.time() + (23 * 3600)

    def search(
        self,
        query: str | None = None,
        *,
        season_number: int | None = None,
        episode_number: int | None = None,
        year: int | None = None,
        languages: str = "en",
        media_type: str | None = None,
    ) -> list[SubtitleSearchResult]:
        """Search for subtitles. Returns results sorted by download count."""
        cache_key = (
            f"{query}|{season_number}|{episode_number}"
            f"|{year}|{languages}|{media_type}"
        )
        if cache_key in self._search_cache:
            return self._search_cache[cache_key]

        params: dict = {"languages": languages}
        if query:
            params["query"] = query
        if season_number is not None:
            params["season_number"] = season_number
        if episode_number is not None:
            params["episode_number"] = episode_number
        if year:
            params["year"] = year
        if media_type:
            params["type"] = media_type

        data = self._get("/subtitles", params=params)
        results = []
        for item in data.get("data", []):
            attrs = item.get("attributes", {})
            files = attrs.get("files", [])
            if not files:
                continue
            file_entry = files[0]
            results.append(SubtitleSearchResult(
                file_id=file_entry["file_id"],
                language=attrs.get("language", languages),
                release=attrs.get("release", ""),
                download_count=attrs.get("download_count", 0),
                from_trusted=attrs.get("from_trusted", False),
            ))

        results.sort(key=lambda r: r.download_count, reverse=True)
        self._search_cache[cache_key] = results
        return results

    def search_by_hash(
        self,
        moviehash: str,
        moviebytesize: int,
        *,
        languages: str = "en",
    ) -> list[SubtitleSearchResult]:
        """Search for subtitles by file hash. Best for exact release matching."""
        cache_key = f"hash|{moviehash}|{moviebytesize}|{languages}"
        if cache_key in self._search_cache:
            return self._search_cache[cache_key]

        params = {
            "moviehash": moviehash,
            "languages": languages,
        }

        data = self._get("/subtitles", params=params)
        results = []
        for item in data.get("data", []):
            attrs = item.get("attributes", {})
            files = attrs.get("files", [])
            if not files:
                continue
            file_entry = files[0]
            results.append(SubtitleSearchResult(
                file_id=file_entry["file_id"],
                language=attrs.get("language", languages),
                release=attrs.get("release", ""),
                download_count=attrs.get("download_count", 0),
                from_trusted=attrs.get("from_trusted", False),
            ))

        results.sort(key=lambda r: r.download_count, reverse=True)
        self._search_cache[cache_key] = results
        return results

    def download(self, file_id: int) -> SubtitleDownloadResult:
        """Request a download link for a subtitle file.

        Requires authentication (username/password).
        Returns a temporary download URL.
        """
        self._ensure_authenticated()

        self._throttle()
        url = f"{self.BASE_URL}/download"
        resp = self._session.post(
            url,
            json={"file_id": file_id},
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        return SubtitleDownloadResult(
            download_url=data["link"],
            file_name=data.get("file_name", "subtitle.srt"),
            remaining=data.get("remaining", 0),
        )

    def download_content(self, download_url: str) -> bytes:
        """Fetch the actual subtitle file content from a temporary URL."""
        self._throttle()
        resp = self._session.get(download_url, timeout=30)
        resp.raise_for_status()
        return resp.content
