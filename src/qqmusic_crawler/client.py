from __future__ import annotations

import time
from typing import Any, Dict, List

import httpx
from loguru import logger
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential


class QQMusicClient:
    def __init__(
        self,
        base_url: str,
        timeout: int = 15,
        max_retries: int = 3,
        rate_limit_qps: float = 1.0,
    ):
        self.base_url = base_url
        self.rate_limit_qps = rate_limit_qps if rate_limit_qps > 0 else 1.0
        self._min_interval = 1.0 / self.rate_limit_qps
        self._last_request_ts = 0.0
        self._max_retries = max_retries
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://y.qq.com/",
                "Origin": "https://y.qq.com",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def _rate_limit(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_ts
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_ts = time.monotonic()

    def _post_json(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        for attempt in Retrying(
            retry=retry_if_exception_type(httpx.HTTPError),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
            stop=stop_after_attempt(self._max_retries),
            reraise=True,
        ):
            with attempt:
                self._rate_limit()
                resp = self._client.post(self.base_url, json=payload)
                resp.raise_for_status()
                return resp.json()
        return {}

    def fetch_artists(self, page: int, page_size: int) -> List[Dict[str, Any]]:
        payload = {
            "comm": {"cv": 0, "ct": 24, "format": "json"},
            "singerList": {
                "module": "Music.SingerListServer",
                "method": "get_singer_list",
                "param": {
                    "area": -100,
                    "sex": -100,
                    "genre": -100,
                    "index": -100,
                    "sin": (page - 1) * page_size,
                    "cur_page": page,
                },
            },
        }
        data = self._post_json(payload)
        return self._extract_artist_items(data)

    def fetch_songs_by_artist(
        self, artist_mid: str, page: int, page_size: int
    ) -> List[Dict[str, Any]]:
        payload = {
            "comm": {"cv": 0, "ct": 24, "format": "json"},
            "singerSongList": {
                "module": "music.web_singer_info_svr",
                "method": "get_singer_detail_info",
                "param": {
                    "sort": 5,
                    "singermid": artist_mid,
                    "sin": (page - 1) * page_size,
                    "num": page_size,
                },
            },
        }
        data = self._post_json(payload)
        return self._extract_song_items(data)

    def fetch_artist_profile(self, artist_mid: str) -> Dict[str, Any]:
        payload = {
            "comm": {"cv": 0, "ct": 24, "format": "json"},
            "singerSongList": {
                "module": "music.web_singer_info_svr",
                "method": "get_singer_detail_info",
                "param": {
                    "sort": 5,
                    "singermid": artist_mid,
                    "sin": 0,
                    "num": 1,
                },
            },
        }
        data = self._post_json(payload)
        return self._extract_artist_profile(data)

    def fetch_toplists(self) -> List[Dict[str, Any]]:
        payload = {
            "comm": {"cv": 0, "ct": 24, "format": "json"},
            "req_1": {
                "module": "musicToplist.ToplistInfoServer",
                "method": "GetAll",
                "param": {},
            },
        }
        data = self._post_json(payload)
        return self._extract_toplists(data)

    def fetch_toplist_detail(self, top_id: int, num: int = 100) -> Dict[str, Any]:
        payload = {
            "comm": {"cv": 0, "ct": 24, "format": "json"},
            "req_1": {
                "module": "musicToplist.ToplistInfoServer",
                "method": "GetDetail",
                "param": {"topid": int(top_id), "offset": 0, "num": int(num), "period": ""},
            },
        }
        data = self._post_json(payload)
        return self._extract_toplist_detail(data)

    def enrich_song_metrics(self, songs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Attach metric fields to songs."""
        song_ids: List[int] = []
        for item in songs:
            if not isinstance(item, dict):
                continue
            sid = item.get("id")
            try:
                sid_int = int(sid)
            except (TypeError, ValueError):
                continue
            if sid_int <= 0 or sid_int in song_ids:
                continue
            song_ids.append(sid_int)

        if not song_ids:
            return songs

        comment_counts = self.fetch_song_comment_counts(song_ids)
        favorite_count_texts = self.fetch_song_favorite_counts(song_ids)

        for item in songs:
            sid = item.get("id")
            try:
                sid_int = int(sid)
            except (TypeError, ValueError):
                continue
            if sid_int in comment_counts:
                item["_metric_comment_count"] = comment_counts[sid_int]
            if sid_int in favorite_count_texts:
                item["_metric_favorite_count_text"] = favorite_count_texts[sid_int]
        return songs

    def fetch_song_comment_counts(self, song_ids: List[int]) -> Dict[int, int]:
        # Note: biz_id must be string, integer payload causes API code=10000.
        req_list = [{"biz_id": str(sid), "biz_type": 1} for sid in song_ids]
        payload = {
            "comm": {"cv": 0, "ct": 24, "format": "json"},
            "songComment": {
                "module": "GlobalComment.GlobalCommentReadServer",
                "method": "GetCommentCount",
                "param": {"request_list": req_list},
            },
        }
        data = self._post_json(payload)
        return self._extract_comment_counts(data)

    def fetch_song_favorite_counts(self, song_ids: List[int]) -> Dict[int, int]:
        payload = {
            "comm": {"cv": 0, "ct": 24, "format": "json"},
            "result": {
                "module": "music.musicasset.SongFavRead",
                "method": "GetSongFansNumberById",
                "param": {"v_songId": song_ids},
            },
        }
        data = self._post_json(payload)
        return self._extract_favorite_counts(data)

    @staticmethod
    def _extract_artist_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        singer_node = data.get("singerList")
        if isinstance(singer_node, dict):
            code = singer_node.get("code")
            if code not in (None, 0):
                logger.warning(
                    "Artist API returned non-zero code={}, subcode={}",
                    code,
                    singer_node.get("subcode"),
                )
        candidates = [
            ("singerList", "data", "singerlist"),
            ("singerList", "data", "list"),
            ("singerList", "list"),
            ("data", "list"),
        ]
        for path in candidates:
            current: Any = data
            ok = True
            for key in path:
                if not isinstance(current, dict) or key not in current:
                    ok = False
                    break
                current = current[key]
            if ok and isinstance(current, list):
                return [x for x in current if isinstance(x, dict)]
        logger.warning("Artist payload shape not matched, keys: {}", list(data.keys()))
        return []

    @staticmethod
    def _extract_song_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        song_node = data.get("singerSongList")
        if isinstance(song_node, dict):
            code = song_node.get("code")
            if code not in (None, 0):
                logger.warning(
                    "Song API returned non-zero code={}, subcode={}",
                    code,
                    song_node.get("subcode"),
                )
        candidates = [
            ("singerSongList", "data", "songlist"),
            ("singerSongList", "data", "song_list"),
            ("singerSongList", "data", "list"),
            ("data", "list"),
        ]
        for path in candidates:
            current: Any = data
            ok = True
            for key in path:
                if not isinstance(current, dict) or key not in current:
                    ok = False
                    break
                current = current[key]
            if ok and isinstance(current, list):
                normalized: List[Dict[str, Any]] = []
                for item in current:
                    if not isinstance(item, dict):
                        continue
                    if "songInfo" in item and isinstance(item["songInfo"], dict):
                        normalized.append(item["songInfo"])
                    else:
                        normalized.append(item)
                return normalized
        logger.warning("Song payload shape not matched, keys: {}", list(data.keys()))
        return []

    @staticmethod
    def _extract_comment_counts(data: Dict[str, Any]) -> Dict[int, int]:
        node = data.get("songComment")
        if isinstance(node, dict):
            code = node.get("code")
            if code not in (None, 0):
                logger.warning(
                    "Comment API returned non-zero code={}, subcode={}",
                    code,
                    node.get("subcode"),
                )
        resp_list = (
            data.get("songComment", {})
            .get("data", {})
            .get("response_list", [])
        )
        result: Dict[int, int] = {}
        if not isinstance(resp_list, list):
            return result

        for item in resp_list:
            if not isinstance(item, dict):
                continue
            sid = item.get("biz_id")
            cnt = item.get("count")
            try:
                sid_i = int(sid)
                cnt_i = int(cnt)
            except (TypeError, ValueError):
                continue
            result[sid_i] = cnt_i
        return result

    @staticmethod
    def _extract_favorite_counts(data: Dict[str, Any]) -> Dict[int, int]:
        node = data.get("result")
        if isinstance(node, dict):
            code = node.get("code")
            if code not in (None, 0):
                logger.warning(
                    "Favorite API returned non-zero code={}, subcode={}",
                    code,
                    node.get("subcode"),
                )

        fav_shows_raw = (
            data.get("result", {})
            .get("data", {})
            .get("m_show", {})
        )

        fav_counts: Dict[int, int] = {}
        if isinstance(fav_shows_raw, dict):
            for sid, value in fav_shows_raw.items():
                try:
                    sid_i = int(sid)
                except (TypeError, ValueError):
                    continue
                fav_counts[sid_i] = QQMusicClient._parse_count_text(str(value))

        return fav_counts

    @staticmethod
    def _extract_artist_profile(data: Dict[str, Any]) -> Dict[str, Any]:
        node = data.get("singerSongList")
        if not isinstance(node, dict):
            return {}
        code = node.get("code")
        if code not in (None, 0):
            logger.warning(
                "Artist profile API returned non-zero code={}, subcode={}",
                code,
                node.get("subcode"),
            )
            return {}
        singer_info = node.get("data", {}).get("singer_info", {})
        total_song_raw = node.get("data", {}).get("total_song")
        if not isinstance(singer_info, dict):
            return {}
        fans_raw = singer_info.get("fans")
        try:
            fans = int(fans_raw)
        except (TypeError, ValueError):
            fans = None
        try:
            total_song = int(total_song_raw)
        except (TypeError, ValueError):
            total_song = 0
        name = singer_info.get("name")
        return {
            "artist_mid": str(singer_info.get("mid") or "").strip(),
            "name": str(name).strip() if name is not None else "",
            "fans": fans,
            "total_song": total_song,
            "raw": singer_info,
        }

    @staticmethod
    def _extract_toplists(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        node = data.get("req_1")
        if not isinstance(node, dict):
            return []
        code = node.get("code")
        if code not in (None, 0):
            logger.warning(
                "Toplist list API returned non-zero code={}, subcode={}",
                code,
                node.get("subcode"),
            )
            return []
        groups = node.get("data", {}).get("group", [])
        if not isinstance(groups, list):
            return []
        toplists: List[Dict[str, Any]] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            items = group.get("toplist", [])
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                top_id = item.get("topId")
                try:
                    top_id_i = int(top_id)
                except (TypeError, ValueError):
                    continue
                toplists.append(
                    {
                        "top_id": top_id_i,
                        "top_name": str(item.get("title") or "").strip(),
                        "period": str(item.get("period") or "").strip(),
                        "update_time": str(item.get("updateTime") or "").strip(),
                        "group_name": str(group.get("groupName") or "").strip(),
                        "raw": item,
                    }
                )
        return toplists

    @staticmethod
    def _extract_toplist_detail(data: Dict[str, Any]) -> Dict[str, Any]:
        node = data.get("req_1")
        if not isinstance(node, dict):
            return {"songs": []}
        code = node.get("code")
        if code not in (None, 0):
            logger.warning(
                "Toplist detail API returned non-zero code={}, subcode={}",
                code,
                node.get("subcode"),
            )
            return {"songs": []}

        detail = node.get("data", {})
        if not isinstance(detail, dict):
            return {"songs": []}

        top_info = detail.get("data", {}) if isinstance(detail.get("data"), dict) else {}
        songs = detail.get("songInfoList", [])
        if not isinstance(songs, list):
            songs = []
        return {
            "top_id": top_info.get("topId"),
            "top_name": top_info.get("title"),
            "period": top_info.get("period"),
            "update_time": top_info.get("updateTime"),
            "songs": songs,
            "raw": detail,
        }

    @staticmethod
    def _parse_count_text(raw: str) -> int:
        text = (raw or "").strip().lower().replace("+", "")
        if not text:
            return 0
        multiplier = 1
        if text.endswith(("k",)):
            multiplier = 1_000
            text = text[:-1]
        elif text.endswith(("w", "万")):
            multiplier = 10_000
            text = text[:-1]
        elif text.endswith(("y", "亿")):
            multiplier = 100_000_000
            text = text[:-1]
        try:
            return int(float(text) * multiplier)
        except ValueError:
            return 0

