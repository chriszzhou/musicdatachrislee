from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Any, Dict, List, Sequence

import httpx
from loguru import logger
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential


class NeteaseMusicClient:
    def __init__(
        self,
        base_url: str = "https://music.163.com",
        timeout: int = 15,
        max_retries: int = 3,
        rate_limit_qps: float = 1.0,
        metric_workers: int = 8,
        metric_batch_size: int = 10,
    ):
        self.base_url = base_url.rstrip("/")
        self.rate_limit_qps = rate_limit_qps if rate_limit_qps > 0 else 1.0
        self._min_interval = 1.0 / self.rate_limit_qps
        self._last_request_ts = 0.0
        self._rate_lock = Lock()
        self._max_retries = max_retries
        self._metric_workers = max(1, int(metric_workers))
        self._metric_batch_size = max(1, int(metric_batch_size))
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://music.163.com/",
                "Origin": "https://music.163.com",
            },
        )

    def close(self) -> None:
        self._client.close()

    def _rate_limit(self) -> None:
        with self._rate_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_ts
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_request_ts = time.monotonic()

    def _get_json(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = "{}{}".format(self.base_url, path)
        for attempt in Retrying(
            retry=retry_if_exception_type(httpx.HTTPError),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
            stop=stop_after_attempt(self._max_retries),
            reraise=True,
        ):
            with attempt:
                self._rate_limit()
                resp = self._client.get(url, params=params)
                resp.raise_for_status()
                payload: Any = resp.json()
                if isinstance(payload, dict):
                    return payload
                if isinstance(payload, str):
                    # Some environments may return JSON string payloads.
                    try:
                        parsed = json.loads(payload)
                        if isinstance(parsed, dict):
                            return parsed
                    except Exception:
                        pass
                    logger.warning(
                        "Netease API returned non-dict JSON string, path={}, sample={}",
                        path,
                        payload[:120],
                    )
                    return {}
                logger.warning(
                    "Netease API returned non-dict payload, path={}, type={}",
                    path,
                    type(payload).__name__,
                )
                return {}
        return {}

    def fetch_artists(self, page: int, page_size: int) -> List[Dict[str, Any]]:
        offset = max(page - 1, 0) * page_size
        data = self._get_json(
            "/api/artist/list",
            {"offset": str(offset), "limit": str(page_size), "total": "true"},
        )
        artists = data.get("artists", [])
        if not isinstance(artists, list):
            return []
        result: List[Dict[str, Any]] = []
        for item in artists:
            if not isinstance(item, dict):
                continue
            result.append(
                {
                    "singer_mid": str(item.get("id") or "").strip(),
                    "singer_name": str(item.get("name") or "").strip(),
                    "region": None,
                    "genre": None,
                    "raw": item,
                }
            )
        return result

    def search_artists_by_name(self, keyword: str, limit: int = 20) -> List[Dict[str, Any]]:
        kw = (keyword or "").strip()
        if not kw:
            return []
        data = self._get_json(
            "/api/search/get/web",
            {"s": kw, "type": "100", "offset": "0", "limit": str(max(limit, 1))},
        )
        result_node = data.get("result", {})
        if isinstance(result_node, str):
            try:
                result_node = json.loads(result_node)
            except Exception:
                result_node = {}
        if not isinstance(result_node, dict):
            result_node = {}
        artists = result_node.get("artists", [])
        if not isinstance(artists, list):
            artists = data.get("artists", []) if isinstance(data.get("artists"), list) else []
        if not artists:
            logger.debug(
                "Netease search empty: keyword={}, code={}, result_keys={}",
                kw,
                data.get("code"),
                list(result_node.keys()) if isinstance(result_node, dict) else "n/a",
            )
            return []
        result: List[Dict[str, Any]] = []
        for item in artists:
            if not isinstance(item, dict):
                continue
            result.append(
                {
                    "singer_mid": str(item.get("id") or "").strip(),
                    "singer_name": str(item.get("name") or "").strip(),
                    "region": None,
                    "genre": None,
                    "raw": item,
                }
            )
        return result

    def fetch_songs_by_artist(
        self, artist_mid: str, page: int, page_size: int
    ) -> List[Dict[str, Any]]:
        offset = max(page - 1, 0) * page_size
        data = self._get_json(
            "/api/v1/artist/songs",
            {"id": str(artist_mid), "offset": str(offset), "limit": str(page_size)},
        )
        songs = data.get("songs", [])
        if not isinstance(songs, list):
            return []
        result: List[Dict[str, Any]] = []
        for item in songs:
            if not isinstance(item, dict):
                continue
            sid = item.get("id")
            sid_str = str(sid or "").strip()
            if not sid_str:
                continue
            album = item.get("album") if isinstance(item.get("album"), dict) else {}
            duration = item.get("duration") or item.get("dt")
            result.append(
                {
                    "id": sid,
                    "mid": sid_str,
                    "name": item.get("name"),
                    "album": {"name": album.get("name")},
                    "duration": duration / 1000 if isinstance(duration, int) else duration,
                    "time_public": None,
                    "artists": item.get("artists"),
                    "starredNum": item.get("starredNum"),
                    "popularity": item.get("popularity"),
                }
            )
        return result

    def fetch_artist_profile(self, artist_mid: str) -> Dict[str, Any]:
        data = self._get_json("/api/v1/artist/{}".format(artist_mid), {})
        artist = data.get("artist", {})
        if not isinstance(artist, dict):
            artist = {}

        # 1) try direct fan fields on artist payload.
        fans = None
        for key in ("followedCnt", "fansCount", "fanCount", "followeds"):
            raw = artist.get(key)
            if raw is None:
                continue
            try:
                fans = int(raw)
            except (TypeError, ValueError):
                fans = None
            break

        # 2) fallback: resolve userId from artist head info, then read profile.followeds.
        if fans is None:
            head = self._get_json("/api/artist/head/info/get", {"id": str(artist_mid)})
            user = head.get("data", {}).get("user", {})
            if isinstance(user, dict):
                user_id = user.get("userId")
                try:
                    user_id_i = int(user_id)
                except (TypeError, ValueError):
                    user_id_i = 0
                if user_id_i > 0:
                    user_detail = self._get_json("/api/v1/user/detail/{}".format(user_id_i), {})
                    profile = user_detail.get("profile", {})
                    if isinstance(profile, dict):
                        raw_followeds = profile.get("followeds")
                        try:
                            fans = int(raw_followeds)
                        except (TypeError, ValueError):
                            fans = None

        return {
            "artist_mid": str(artist.get("id") or "").strip(),
            "name": str(artist.get("name") or "").strip(),
            "fans": fans,
            "total_song": int(artist.get("musicSize") or 0),
            "raw": artist,
        }

    def enrich_song_metrics(self, songs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not songs:
            return songs

        song_tasks: List[Dict[str, Any]] = []
        for item in songs:
            sid = item.get("id")
            try:
                sid_i = int(sid)
            except (TypeError, ValueError):
                continue
            song_tasks.append({"item": item, "song_id": sid_i})

        total = len(song_tasks)
        if total == 0:
            return songs

        logger.info(
            "Start metric enrichment: songs={}, workers={}, qps={}, batch_size={}",
            total,
            self._metric_workers,
            self.rate_limit_qps,
            self._metric_batch_size,
        )
        started_at = time.monotonic()

        song_ids = [task["song_id"] for task in song_tasks]
        comment_counts = self._fetch_song_comment_counts_batch(song_ids)
        for task in song_tasks:
            sid_i = task["song_id"]
            task["item"]["_metric_comment_count"] = int(comment_counts.get(sid_i, 0))

        logger.info("Comment batch fetch done: songs={}", total)

        def _enrich_favorite_only(task: Dict[str, Any]) -> None:
            item = task["item"]
            sid_i = task["song_id"]
            fav_i = self._fetch_song_red_count(sid_i)
            if fav_i <= 0:
                fav_raw = item.get("starredNum")
                try:
                    fav_i = int(fav_raw)
                except (TypeError, ValueError):
                    fav_i = 0
            item["_metric_favorite_count_text"] = fav_i

        completed = 0
        progress_step = 10
        with ThreadPoolExecutor(max_workers=self._metric_workers) as executor:
            futures = [executor.submit(_enrich_favorite_only, task) for task in song_tasks]
            for fut in as_completed(futures):
                fut.result()
                completed += 1
                if completed % progress_step == 0 or completed == total:
                    logger.info("Favorite fetch progress: {}/{}", completed, total)

        elapsed = time.monotonic() - started_at
        logger.info("Metric enrichment done: songs={}, elapsed={:.2f}s", total, elapsed)
        return songs

    @staticmethod
    def _chunked(items: Sequence[int], size: int) -> List[List[int]]:
        chunks: List[List[int]] = []
        for i in range(0, len(items), size):
            chunks.append(list(items[i : i + size]))
        return chunks

    def _fetch_song_comment_counts_batch(self, song_ids: List[int]) -> Dict[int, int]:
        result: Dict[int, int] = {}
        chunks = self._chunked(song_ids, self._metric_batch_size)
        for chunk in chunks:
            payload: Dict[str, str] = {}
            for sid in chunk:
                payload["/api/v1/resource/comments/R_SO_4_{}".format(sid)] = (
                    '{{"rid":"{}","limit":1,"offset":0,"beforeTime":0}}'.format(sid)
                )

            data = self._get_json("/api/batch", payload)
            if int(data.get("code") or 0) != 200:
                for sid in chunk:
                    result[sid] = self._fetch_song_comment_count(sid)
                continue

            for sid in chunk:
                key = "/api/v1/resource/comments/R_SO_4_{}".format(sid)
                node = data.get(key, {})
                if not isinstance(node, dict):
                    result[sid] = 0
                    continue
                try:
                    result[sid] = int(node.get("total") or 0)
                except (TypeError, ValueError):
                    result[sid] = 0
        return result

    def _fetch_song_comment_count(self, song_id: int) -> int:
        data = self._get_json(
            "/api/v1/resource/comments/R_SO_4_{}".format(song_id),
            {"limit": "1", "offset": "0"},
        )
        try:
            return int(data.get("total") or 0)
        except (TypeError, ValueError):
            return 0

    def _fetch_song_red_count(self, song_id: int) -> int:
        data = self._get_json("/api/song/red/count", {"songId": str(song_id)})
        red_data = data.get("data", {})
        if not isinstance(red_data, dict):
            return 0
        try:
            return int(red_data.get("count") or 0)
        except (TypeError, ValueError):
            return 0

    def fetch_toplists(self) -> List[Dict[str, Any]]:
        data = self._get_json("/api/toplist", {})
        items = data.get("list", [])
        if not isinstance(items, list):
            return []
        result: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                top_id = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            result.append(
                {
                    "top_id": top_id,
                    "top_name": str(item.get("name") or "").strip(),
                    "period": str(item.get("updateFrequency") or "").strip(),
                    "update_time": str(item.get("updateTime") or ""),
                    "group_name": "",
                    "raw": item,
                }
            )
        return result

    def fetch_toplist_detail(self, top_id: int, num: int = 100) -> Dict[str, Any]:
        data = self._get_json("/api/v6/playlist/detail", {"id": str(top_id)})
        if int(data.get("code") or 0) != 200:
            logger.warning("Netease toplist detail failed, top_id={}, code={}", top_id, data.get("code"))
            return {"songs": []}
        playlist = data.get("playlist", {})
        if not isinstance(playlist, dict):
            return {"songs": []}
        tracks = playlist.get("tracks", [])
        if not isinstance(tracks, list):
            tracks = []
        tracks = tracks[: max(int(num), 0)]
        songs: List[Dict[str, Any]] = []
        for item in tracks:
            if not isinstance(item, dict):
                continue
            artists = item.get("ar", [])
            if not isinstance(artists, list):
                artists = []
            singer_list = []
            for a in artists:
                if not isinstance(a, dict):
                    continue
                singer_list.append({"mid": str(a.get("id") or "").strip(), "name": a.get("name")})
            album = item.get("al") if isinstance(item.get("al"), dict) else {}
            songs.append(
                {
                    "id": item.get("id"),
                    "mid": str(item.get("id") or "").strip(),
                    "name": item.get("name"),
                    "title": item.get("name"),
                    "singer": singer_list,
                    "album": {"name": album.get("name")},
                }
            )
        return {
            "top_id": top_id,
            "top_name": str(playlist.get("name") or "").strip(),
            "period": str(playlist.get("updateFrequency") or "").strip(),
            "update_time": str(playlist.get("updateTime") or ""),
            "songs": songs,
            "raw": playlist,
        }
