from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Artist(Base):
    __tablename__ = "artists"

    artist_mid: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    fans: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    genre: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now()
    )

    songs: Mapped[List["Song"]] = relationship(back_populates="artist")


class Song(Base):
    __tablename__ = "songs"

    song_mid: Mapped[str] = mapped_column(String(64), primary_key=True)
    song_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    artist_mid: Mapped[str] = mapped_column(
        String(64), ForeignKey("artists.artist_mid"), nullable=False, index=True
    )
    album_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    duration: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    publish_time: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    comment_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    favorite_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    favorite_count_text: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mixsongid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now()
    )

    artist: Mapped[Artist] = relationship(back_populates="songs")


def to_json(raw: Dict[str, Any]) -> str:
    return json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
