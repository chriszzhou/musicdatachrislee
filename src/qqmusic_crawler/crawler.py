from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from .client import QQMusicClient
from .storage import Storage


class CrawlerService:
    def __init__(self, client: QQMusicClient, storage: Optional[Storage] = None):
        self.client = client
        self.storage = storage

    def _require_storage(self) -> Storage:
        if self.storage is None:
            raise ValueError("This operation requires snapshot storage.")
        return self.storage

    def crawl_songs_for_artists(
        self, artist_mids: List[str], song_pages: int, page_size: int
    ) -> int:
        storage = self._require_storage()
        cleaned_mids = []
        for mid in artist_mids:
            m = (mid or "").strip()
            if not m or m in cleaned_mids:
                continue
            cleaned_mids.append(m)

        for mid in cleaned_mids:
            storage.ensure_artist_stub(mid)

        logger.info("Start songs crawl for {} artists", len(cleaned_mids))

        total = 0
        for artist_mid in cleaned_mids:
            profile = self.client.fetch_artist_profile(artist_mid)
            profile_name = str(profile.get("name") or "").strip() or None
            profile_fans = profile.get("fans")
            storage.ensure_artist_stub(
                artist_mid,
                name=profile_name,
                fans=int(profile_fans) if profile_fans is not None else None,
            )
            if profile_fans is not None:
                logger.info("Artist {} fans={}", artist_mid, profile_fans)

            artist_total = 0
            for page in range(1, song_pages + 1):
                songs = self.client.fetch_songs_by_artist(
                    artist_mid=artist_mid,
                    page=page,
                    page_size=page_size,
                )
                songs = self.client.enrich_song_metrics(songs)
                saved = storage.upsert_songs(songs, artist_mid=artist_mid)
                artist_total += saved
                total += saved
                logger.info(
                    "Songs artist={} page={} fetched={}, saved={}",
                    artist_mid,
                    page,
                    len(songs),
                    saved,
                )
                if not songs:
                    break
            logger.info("Artist {} songs saved={}", artist_mid, artist_total)

        logger.success("Songs crawl finished, total_saved={}", total)
        return total

    def find_artist_candidates_by_name(
        self, keyword: str, max_pages: int = 5, page_size: int = 80
    ) -> List[Dict[str, str]]:
        """Search singer list pages and return matched artist candidates."""
        kw = (keyword or "").strip().lower()
        if not kw:
            return []

        # Prefer direct search API when client provides one.
        search_func = getattr(self.client, "search_artists_by_name", None)
        direct_found: List[Dict[str, str]] = []
        if callable(search_func):
            items = search_func(keyword, limit=max(page_size, 20))
            seen_direct = set()
            for item in items:
                if not isinstance(item, dict):
                    continue
                mid = str(item.get("singer_mid") or item.get("mid") or "").strip()
                name = str(item.get("singer_name") or item.get("name") or "").strip()
                if not mid or not name:
                    continue
                if kw not in name.lower():
                    continue
                if mid in seen_direct:
                    continue
                direct_found.append({"artist_mid": mid, "name": name})
                seen_direct.add(mid)
            if direct_found:
                direct_found.sort(key=lambda x: (x["name"].lower() != kw, x["name"].lower()))
                return direct_found
            # Search returned empty: fall back to listing artists and filtering by keyword.

        found: List[Dict[str, str]] = []
        seen_mids = set()

        for page in range(1, max_pages + 1):
            items = self.client.fetch_artists(page=page, page_size=page_size)
            if not items:
                break

            for item in items:
                mid = str(item.get("singer_mid") or item.get("mid") or "").strip()
                name = str(item.get("singer_name") or item.get("name") or "").strip()
                if not mid or not name:
                    continue

                name_l = name.lower()
                if kw not in name_l:
                    continue
                if mid in seen_mids:
                    continue

                found.append({"artist_mid": mid, "name": name})
                seen_mids.add(mid)

            # Fuzzy search: if matches are enough, no need to scan too many pages.
            if len(found) >= 10:
                break

        found.sort(key=lambda x: (x["name"].lower() != kw, x["name"].lower()))
        return found

    def find_artist_toplist_hits(self, artist_mid: str, top_n: int = 100) -> List[Dict[str, Any]]:
        mid = (artist_mid or "").strip()
        if not mid:
            return []

        toplists = self.client.fetch_toplists()
        hits: List[Dict[str, Any]] = []
        for toplist in toplists:
            top_id = toplist.get("top_id")
            if not isinstance(top_id, int):
                continue
            detail = self.client.fetch_toplist_detail(top_id=top_id, num=top_n)
            songs = detail.get("songs", [])
            if not isinstance(songs, list):
                continue

            for idx, song in enumerate(songs):
                if not isinstance(song, dict):
                    continue
                singers = song.get("singer", [])
                if not isinstance(singers, list):
                    continue
                singer_mids = [
                    str(s.get("mid") or "").strip()
                    for s in singers
                    if isinstance(s, dict)
                ]
                if mid not in singer_mids:
                    continue

                top_name = str(detail.get("top_name") or toplist.get("top_name") or "").strip()
                top_period = str(detail.get("period") or toplist.get("period") or "").strip()
                top_update_time = str(
                    detail.get("update_time") or toplist.get("update_time") or ""
                ).strip()
                song_mid = str(song.get("mid") or song.get("songmid") or "").strip()
                song_id = song.get("id")
                album = song.get("album") if isinstance(song.get("album"), dict) else {}
                album_name = str(album.get("name") or song.get("albumname") or "").strip()
                singer_names = ",".join(
                    [
                        str(s.get("name") or "").strip()
                        for s in singers
                        if isinstance(s, dict) and str(s.get("name") or "").strip()
                    ]
                )
                hits.append(
                    {
                        "top_id": top_id,
                        "top_name": top_name,
                        "top_period": top_period,
                        "top_update_time": top_update_time,
                        "rank": idx + 1,
                        "song_mid": song_mid,
                        "song_id": song_id,
                        "song_name": str(song.get("name") or song.get("title") or "").strip(),
                        "album_name": album_name,
                        "singer_names": singer_names,
                        "raw_json": song,
                    }
                )

        hits.sort(key=lambda x: (str(x.get("top_name") or ""), int(x.get("rank") or 0)))
        return hits
