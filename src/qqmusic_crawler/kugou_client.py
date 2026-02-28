from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Any, Dict, List

import httpx
from loguru import logger
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential


class KugouMusicClient:
    _APPID = 1005
    _CLIENTVER = 20489
    _ANDROID_SIGN_SECRET = "OIlwieks28dk2k092lksi2UIkp"
    _WEB_SIGN_SECRET = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt"

    def __init__(
        self,
        base_url: str = "http://mobilecdn.kugou.com",
        timeout: int = 15,
        max_retries: int = 3,
        rate_limit_qps: float = 5.0,
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
                "Referer": "https://m.kugou.com/",
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

    def _get_json(self, path: str, params: Dict[str, Any], host: str = "") -> Dict[str, Any]:
        root = host.rstrip("/") if host else self.base_url
        url = "{}{}".format(root, path)
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
                return resp.json()
        return {}

    @staticmethod
    def _md5(text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def _signature_web(self, params: Dict[str, Any]) -> str:
        params_string = "".join(
            sorted(["{}={}".format(k, params[k]) for k in params.keys()])
        )
        return self._md5(
            "{}{}{}".format(self._WEB_SIGN_SECRET, params_string, self._WEB_SIGN_SECRET)
        )

    def _signature_android(self, params: Dict[str, Any], data: str = "") -> str:
        parts: List[str] = []
        for k in sorted(params.keys()):
            v = params[k]
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
            parts.append("{}={}".format(k, v))
        params_string = "".join(parts)
        return self._md5(
            "{}{}{}{}".format(
                self._ANDROID_SIGN_SECRET, params_string, data or "", self._ANDROID_SIGN_SECRET
            )
        )

    def _signed_default_params(self) -> Dict[str, Any]:
        clienttime = int(time.time())
        return {
            "dfid": "-",
            "mid": "undefined",
            "uuid": "-",
            "appid": self._APPID,
            "clientver": self._CLIENTVER,
            "clienttime": clienttime,
        }

    def _signed_common_headers(self, clienttime: int) -> Dict[str, str]:
        return {
            "User-Agent": "Android15-1070-11083-46-0-DiscoveryDRADProtocol-wifi",
            "dfid": "-",
            "mid": "undefined",
            "clienttime": str(clienttime),
            "kg-rc": "1",
            "kg-thash": "5d816a0",
            "kg-rec": "1",
            "kg-rf": "B9EDA08A64250DEFFBCADDEE00F8F25F",
        }

    def _signed_get_web(
        self,
        path: str,
        params: Dict[str, Any],
        x_router: str,
    ) -> Dict[str, Any]:
        default = self._signed_default_params()
        merged = {**default, **params}
        merged["signature"] = self._signature_web(merged)
        headers = self._signed_common_headers(int(default["clienttime"]))
        headers["x-router"] = x_router
        url = "https://gateway.kugou.com{}".format(path)
        for attempt in Retrying(
            retry=retry_if_exception_type(httpx.HTTPError),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
            stop=stop_after_attempt(self._max_retries),
            reraise=True,
        ):
            with attempt:
                self._rate_limit()
                resp = self._client.get(url, params=merged, headers=headers)
                resp.raise_for_status()
                return resp.json()
        return {}

    def _signed_get_android(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        default = self._signed_default_params()
        merged = {**default, **params}
        merged["signature"] = self._signature_android(merged, "")
        headers = self._signed_common_headers(int(default["clienttime"]))
        url = "https://gateway.kugou.com{}".format(path)
        for attempt in Retrying(
            retry=retry_if_exception_type(httpx.HTTPError),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
            stop=stop_after_attempt(self._max_retries),
            reraise=True,
        ):
            with attempt:
                self._rate_limit()
                resp = self._client.get(url, params=merged, headers=headers)
                resp.raise_for_status()
                return resp.json()
        return {}

    def _signed_post_android(
        self,
        path: str,
        payload: Dict[str, Any],
        x_router: str = "",
        extra_headers: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        default = self._signed_default_params()
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        params = {**default}
        params["signature"] = self._signature_android(params, body)
        headers = self._signed_common_headers(int(default["clienttime"]))
        headers["Content-Type"] = "application/json"
        if x_router:
            headers["x-router"] = x_router
        if extra_headers:
            headers.update(extra_headers)
        url = "https://gateway.kugou.com{}".format(path)
        for attempt in Retrying(
            retry=retry_if_exception_type(httpx.HTTPError),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
            stop=stop_after_attempt(self._max_retries),
            reraise=True,
        ):
            with attempt:
                self._rate_limit()
                resp = self._client.post(url, params=params, headers=headers, content=body.encode("utf-8"))
                resp.raise_for_status()
                return resp.json()
        return {}


    def fetch_artists(self, page: int, page_size: int) -> List[Dict[str, Any]]:
        # Public singer list endpoint is unstable; use empty fallback.
        return []

    def search_artists_by_name(self, keyword: str, limit: int = 20) -> List[Dict[str, Any]]:
        kw = (keyword or "").strip()
        if not kw:
            return []
        data = self._get_json(
            "/api/v3/search/singer",
            {
                "format": "json",
                "keyword": kw,
                "page": "1",
                "pagesize": str(max(1, limit)),
                "showtype": "1",
            },
        )
        items = data.get("data", [])
        if not isinstance(items, list):
            return []
        result: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            singer_id = str(item.get("singerid") or "").strip()
            singer_name = str(item.get("singername") or "").strip()
            if not singer_id or not singer_name:
                continue
            result.append(
                {
                    "singer_mid": singer_id,
                    "singer_name": singer_name,
                    "region": None,
                    "genre": None,
                    "raw": item,
                }
            )
        return result

    def fetch_songs_by_artist(
        self, artist_mid: str, page: int, page_size: int
    ) -> List[Dict[str, Any]]:
        data = self._get_json(
            "/api/v3/singer/song",
            {
                "format": "json",
                "singerid": str(artist_mid),
                "page": str(max(page, 1)),
                "pagesize": str(max(page_size, 1)),
            },
        )
        items = data.get("data", {}).get("info", [])
        if not isinstance(items, list):
            return []
        result: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            song_hash = str(item.get("hash") or "").strip()
            if not song_hash:
                continue
            filename = str(item.get("filename") or "").strip()
            song_name = str(item.get("songname") or "").strip()
            if not song_name:
                if " - " in filename:
                    song_name = filename.split(" - ", 1)[1].strip()
                else:
                    song_name = filename
            album_name = str(item.get("album_name") or "").strip()
            authors = item.get("authors")
            if not isinstance(authors, list):
                authors = []
            audio_id = item.get("audio_id")
            try:
                song_id = int(audio_id)
            except (TypeError, ValueError):
                song_id = None
            mixsongid_raw = item.get("album_audio_id") or item.get("audio_id")
            try:
                mixsongid = int(mixsongid_raw)
            except (TypeError, ValueError):
                mixsongid = None
            result.append(
                {
                    "id": song_id,
                    "mid": song_hash,
                    "name": song_name,
                    "album": {"name": album_name},
                    "duration": item.get("duration"),
                    "time_public": item.get("publish_date"),
                    "artists": authors,
                    "mixsongid": mixsongid,
                    "raw_filename": filename,
                    "raw": item,
                }
            )
        return result

    def fetch_artist_profile(self, artist_mid: str) -> Dict[str, Any]:
        data = self._get_json(
            "/api/v3/singer/info",
            {"format": "json", "singerid": str(artist_mid)},
        )
        item = data.get("data", {})
        if not isinstance(item, dict):
            item = {}
        fans = None
        signed_detail = self._signed_post_android(
            "/kmr/v3/author",
            {"author_id": str(artist_mid)},
            x_router="openapi.kugou.com",
            extra_headers={"kg-tid": "36"},
        )
        signed_data = signed_detail.get("data", {})
        if isinstance(signed_data, dict):
            raw_fans = signed_data.get("fansnums")
            try:
                fans = int(raw_fans)
            except (TypeError, ValueError):
                fans = None
        return {
            "artist_mid": str(item.get("singerid") or "").strip(),
            "name": str(item.get("singername") or "").strip(),
            "fans": fans,
            "total_song": int(item.get("songcount") or 0),
            "raw": item,
        }

    def enrich_song_metrics(self, songs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not songs:
            return songs

        logger.info(
            "Kugou metric enrichment start: songs={}, workers={}, batch_size={}, qps={}",
            len(songs),
            self._metric_workers,
            self._metric_batch_size,
            self.rate_limit_qps,
        )
        started_at = time.monotonic()

        song_hashes: List[str] = []
        mixsongids: List[int] = []
        for item in songs:
            song_hash = str(item.get("mid") or "").strip()
            if song_hash:
                song_hashes.append(song_hash)
            mixsongid = item.get("mixsongid")
            try:
                mixsongids.append(int(mixsongid))
            except (TypeError, ValueError):
                continue

        favorite_map = self._fetch_song_favorite_counts_batch(mixsongids)
        logger.info(
            "Kugou metric enrichment batched done: favorite_keys={}",
            len(favorite_map),
        )

        comment_map: Dict[str, int] = {}
        progress_step = 10
        completed = 0
        with ThreadPoolExecutor(max_workers=self._metric_workers) as executor:
            future_map = {
                executor.submit(self._fetch_song_comment_count, song_hash): song_hash
                for song_hash in song_hashes
            }
            for fut in as_completed(future_map):
                song_hash = future_map[fut]
                comment_map[song_hash] = fut.result()
                completed += 1
                if completed % progress_step == 0 or completed == len(song_hashes):
                    logger.info(
                        "Kugou comment fetch progress: {}/{}",
                        completed,
                        len(song_hashes),
                    )

        for item in songs:
            song_hash = str(item.get("mid") or "").strip().upper()
            try:
                mixsongid = int(item.get("mixsongid"))
            except (TypeError, ValueError):
                mixsongid = 0
            item["_metric_comment_count"] = int(comment_map.get(song_hash, 0))
            item["_metric_favorite_count_text"] = int(favorite_map.get(mixsongid, 0))

        elapsed = time.monotonic() - started_at
        logger.info("Kugou metric enrichment done: songs={}, elapsed={:.2f}s", len(songs), elapsed)
        return songs

    def _fetch_song_comment_count(self, song_hash: str) -> int:
        if not song_hash:
            return 0
        data = self._signed_get_web(
            "/index.php",
            {"r": "comments/getcommentsnum", "code": "fc4be23b4e972707f36b8a828a93ba8a", "hash": song_hash},
            x_router="sum.comment.service.kugou.com",
        )
        value = data.get(song_hash)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _fetch_song_favorite_count(self, mixsongid: Any) -> int:
        try:
            mid = int(mixsongid)
        except (TypeError, ValueError):
            return 0
        data = self._signed_get_android(
            "/count/v1/audio/mget_collect",
            {"mixsongids": str(mid)},
        )
        rows = data.get("data", {}).get("list", [])
        if not isinstance(rows, list) or not rows:
            return 0
        row = rows[0] if isinstance(rows[0], dict) else {}
        try:
            return int(row.get("count") or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _chunked_ints(items: List[int], size: int) -> List[List[int]]:
        chunks: List[List[int]] = []
        for i in range(0, len(items), size):
            chunks.append(items[i : i + size])
        return chunks

    @staticmethod
    def _chunked_strs(items: List[str], size: int) -> List[List[str]]:
        chunks: List[List[str]] = []
        for i in range(0, len(items), size):
            chunks.append(items[i : i + size])
        return chunks

    def _fetch_song_favorite_counts_batch(self, mixsongids: List[int]) -> Dict[int, int]:
        result: Dict[int, int] = {}
        unique_ids = list(dict.fromkeys([x for x in mixsongids if x > 0]))
        if not unique_ids:
            return result
        for chunk in self._chunked_ints(unique_ids, self._metric_batch_size):
            data = self._signed_get_android(
                "/count/v1/audio/mget_collect",
                {"mixsongids": ",".join([str(x) for x in chunk])},
            )
            rows = data.get("data", {}).get("list", [])
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    mid = int(row.get("mixsongid") or 0)
                    cnt = int(row.get("count") or 0)
                except (TypeError, ValueError):
                    continue
                if mid > 0:
                    result[mid] = cnt
        return result

    def _fetch_song_heats_batch(self, song_hashes: List[str]) -> Dict[str, int]:
        return {}

    def fetch_toplists(self) -> List[Dict[str, Any]]:
        data = self._get_json("/rank/list", {"json": "true"}, host="https://m.kugou.com")
        items = data.get("rank", {}).get("list", [])
        if not isinstance(items, list):
            return []
        result: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                top_id = int(item.get("rankid"))
            except (TypeError, ValueError):
                continue
            result.append(
                {
                    "top_id": top_id,
                    "top_name": str(item.get("rankname") or "").strip(),
                    "period": str(item.get("update_frequency") or "").strip(),
                    "update_time": str(item.get("pubtime") or "").strip(),
                    "group_name": str(item.get("classify") or "").strip(),
                    "raw": item,
                }
            )
        return result

    def fetch_toplist_detail(self, top_id: int, num: int = 100) -> Dict[str, Any]:
        data = self._get_json(
            "/rank/info/",
            {"rankid": str(top_id), "page": "1", "json": "true"},
            host="https://m.kugou.com",
        )
        songs_node = data.get("songs", {})
        songs_list = songs_node.get("list", []) if isinstance(songs_node, dict) else []
        if not isinstance(songs_list, list):
            songs_list = []
        songs_list = songs_list[: max(int(num), 0)]
        songs: List[Dict[str, Any]] = []
        for item in songs_list:
            if not isinstance(item, dict):
                continue
            song_hash = str(item.get("hash") or "").strip()
            song_name = str(item.get("songname") or "").strip()
            authors = item.get("authors") if isinstance(item.get("authors"), list) else []
            singers = []
            for a in authors:
                if not isinstance(a, dict):
                    continue
                singers.append(
                    {
                        "mid": str(a.get("author_id") or "").strip(),
                        "name": str(a.get("author_name") or "").strip(),
                    }
                )
            songs.append(
                {
                    "id": item.get("audio_id"),
                    "mid": song_hash,
                    "name": song_name,
                    "title": song_name,
                    "singer": singers,
                    "album": {"name": str(item.get("album_name") or "").strip()},
                }
            )
        info = data.get("info", {}) if isinstance(data.get("info"), dict) else {}
        return {
            "top_id": top_id,
            "top_name": str(info.get("rankname") or "").strip(),
            "period": str(info.get("update_frequency") or "").strip(),
            "update_time": str(info.get("pubtime") or "").strip(),
            "songs": songs,
            "raw": data,
        }
