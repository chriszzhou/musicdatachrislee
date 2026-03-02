from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from .models import Artist, Base, Song, to_json


class Storage:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self._ensure_sqlite_parent(database_url)
        self.engine = create_engine(database_url, future=True)

    @staticmethod
    def _ensure_sqlite_parent(database_url: str) -> None:
        if not database_url.startswith("sqlite:///"):
            return
        relative_path = database_url.replace("sqlite:///", "", 1)
        Path(relative_path).parent.mkdir(parents=True, exist_ok=True)

    def create_tables(self) -> None:
        Base.metadata.create_all(self.engine)
        self._apply_lightweight_migrations()

    def _apply_lightweight_migrations(self) -> None:
        """Apply additive SQLite schema migrations for existing local DB."""
        if not self.database_url.startswith("sqlite:///"):
            return

        with self.engine.begin() as conn:
            artist_cols = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(artists)")).fetchall()
            }
            if "fans" not in artist_cols:
                conn.execute(text("ALTER TABLE artists ADD COLUMN fans INTEGER"))

            cols = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(songs)")).fetchall()
            }
            if "song_id" not in cols:
                conn.execute(text("ALTER TABLE songs ADD COLUMN song_id INTEGER"))
            if "comment_count" not in cols:
                conn.execute(text("ALTER TABLE songs ADD COLUMN comment_count INTEGER"))
            if "favorite_count" not in cols:
                conn.execute(text("ALTER TABLE songs ADD COLUMN favorite_count INTEGER"))
            if "favorite_count_text" not in cols:
                conn.execute(text("ALTER TABLE songs ADD COLUMN favorite_count_text INTEGER"))
            if "mixsongid" not in cols:
                conn.execute(text("ALTER TABLE songs ADD COLUMN mixsongid INTEGER"))

    def upsert_artists(self, artists: Iterable[Dict[str, Any]]) -> int:
        count = 0
        with Session(self.engine) as session:
            for item in artists:
                artist_mid_raw = item.get("singer_mid") or item.get("mid") or ""
                artist_mid = str(artist_mid_raw).strip()
                if not artist_mid:
                    continue
                name = (
                    item.get("singer_name")
                    or item.get("name")
                    or item.get("title")
                    or "unknown"
                ).strip()
                artist = Artist(
                    artist_mid=artist_mid,
                    name=name,
                    fans=item.get("fans"),
                    region=str(item.get("region") or "") or None,
                    genre=str(item.get("genre") or "") or None,
                    raw_json=to_json(item),
                )
                session.merge(artist)
                count += 1
            session.commit()
        return count

    def upsert_songs(self, songs: Iterable[Dict[str, Any]], artist_mid: str) -> int:
        count = 0
        with Session(self.engine) as session:
            for item in songs:
                song_mid_raw = (
                    item.get("songmid")
                    or item.get("mid")
                    or item.get("id")
                    or ""
                )
                song_mid = str(song_mid_raw).strip()
                if not song_mid:
                    continue
                title = (
                    item.get("songname")
                    or item.get("name")
                    or item.get("title")
                    or "unknown"
                ).strip()
                album_name = None
                album_data = item.get("album")
                if isinstance(album_data, dict):
                    album_name = album_data.get("name")
                if not album_name:
                    album_name = item.get("albumname")

                mixsongid_raw = item.get("mixsongid")
                mixsongid_val = None
                if mixsongid_raw is not None:
                    try:
                        mixsongid_val = int(mixsongid_raw)
                    except (TypeError, ValueError):
                        pass

                song = Song(
                    song_mid=song_mid,
                    song_id=item.get("id"),
                    name=title,
                    artist_mid=artist_mid,
                    album_name=album_name,
                    duration=item.get("interval") or item.get("duration"),
                    publish_time=item.get("time_public") or item.get("pubtime"),
                    comment_count=item.get("_metric_comment_count"),
                    favorite_count=None,
                    favorite_count_text=item.get("_metric_favorite_count_text"),
                    mixsongid=mixsongid_val,
                    raw_json=to_json(item),
                )
                session.merge(song)
                count += 1
            session.commit()
        return count

    def ensure_artist_stub(
        self, artist_mid: str, name: Optional[str] = None, fans: Optional[int] = None
    ) -> None:
        """Create or update a minimal artist row for snapshot crawling."""
        mid = (artist_mid or "").strip()
        if not mid:
            return
        with Session(self.engine) as session:
            artist = session.get(Artist, mid)
            if artist is None:
                session.add(
                    Artist(
                        artist_mid=mid,
                        name=(name or mid).strip() or mid,
                        fans=fans,
                        region=None,
                        genre=None,
                        raw_json="{}",
                    )
                )
            else:
                if name and name.strip():
                    artist.name = name.strip()
                if fans is not None:
                    artist.fans = int(fans)
            session.commit()

    def list_artist_mids(self, limit: Optional[int] = None) -> List[str]:
        with Session(self.engine) as session:
            stmt = select(Artist.artist_mid).order_by(Artist.name.asc())
            if limit and limit > 0:
                stmt = stmt.limit(limit)
            return list(session.scalars(stmt))
