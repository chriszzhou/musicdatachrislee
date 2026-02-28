from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Tuple

from loguru import logger

from .config import settings
from .crawler import CrawlerService
from .kugou_client import KugouMusicClient
from .storage import Storage
from .toplist_storage import query_artist_toplist_hits, upsert_artist_toplist_hits
from .tracking import report_changes, track_changes_for_artist


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KugouMusic crawler CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_find = sub.add_parser("find-artist", help="find artist ids by artist name")
    p_find.add_argument("--name", required=True, help="artist name keyword")
    p_find.add_argument("--max-pages", type=int, default=5)
    p_find.add_argument(
        "--artist-page-size", type=int, default=settings.qqmusic_default_artist_page_size
    )

    p_track = sub.add_parser(
        "crawl-track",
        help="crawl one artist into snapshot DB and track changes",
    )
    p_track.add_argument("--artist-mid", default="", help="target artist id")
    p_track.add_argument("--artist-name", default="", help="target artist name")
    p_track.add_argument("--song-pages", type=int, default=2)
    p_track.add_argument(
        "--song-page-size", type=int, default=settings.qqmusic_default_song_page_size
    )
    p_track.add_argument("--snapshots-dir", default="data/kugou_snapshots")
    p_track.add_argument("--changes-db", default="data/kugou_changes.db")

    p_report = sub.add_parser("report-changes", help="report changes by date")
    p_report.add_argument("--date", default="", help="date in YYYY-MM-DD")
    p_report.add_argument("--month", default="", help="month in YYYY-MM")
    p_report.add_argument("--year", default="", help="year in YYYY")
    p_report.add_argument("--artist-mid", default="", help="optional artist id filter")
    p_report.add_argument("--changes-db", default="data/kugou_changes.db")
    p_report.add_argument("--limit", type=int, default=200)

    p_toplist = sub.add_parser(
        "check-artist-toplist",
        help="check whether an artist has songs on toplists",
    )
    p_toplist.add_argument("--artist-mid", default="", help="target artist id")
    p_toplist.add_argument("--artist-name", default="", help="target artist name")
    p_toplist.add_argument("--top-n", type=int, default=100)
    p_toplist.add_argument("--toplist-db", default="data/kugou_toplist.db")
    p_toplist.add_argument("--limit", type=int, default=200)
    return parser


def build_client() -> KugouMusicClient:
    return KugouMusicClient(
        base_url=settings.kugou_base_url,
        timeout=settings.qqmusic_timeout,
        max_retries=settings.qqmusic_max_retries,
        rate_limit_qps=settings.kugou_rate_limit_qps,
        metric_workers=settings.kugou_metric_workers,
        metric_batch_size=settings.kugou_metric_batch_size,
    )


def build_snapshot_service(database_url: str) -> Tuple[KugouMusicClient, CrawlerService]:
    storage = Storage(database_url)
    storage.create_tables()
    client = build_client()
    return client, CrawlerService(client=client, storage=storage)


def main() -> int:
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    parser = build_parser()
    args = parser.parse_args()

    client = build_client()
    service = CrawlerService(client=client)
    try:
        if args.command == "find-artist":
            candidates = service.find_artist_candidates_by_name(
                keyword=args.name, max_pages=args.max_pages, page_size=args.artist_page_size
            )
            if not candidates:
                logger.warning("No artist matched by name: {}", args.name)
            else:
                logger.info("Matched {} candidates:", len(candidates))
                for item in candidates:
                    logger.info("name='{}' id={}", item["name"], item["artist_mid"])
        elif args.command == "crawl-track":
            artist_mid = (args.artist_mid or "").strip()
            if not artist_mid:
                artist_name = (args.artist_name or "").strip()
                if not artist_name:
                    logger.error("crawl-track requires --artist-mid or --artist-name")
                    return 1
                candidates = service.find_artist_candidates_by_name(
                    keyword=artist_name,
                    max_pages=8,
                    page_size=settings.qqmusic_default_artist_page_size,
                )
                if not candidates:
                    logger.error("No artist matched by name: {}", artist_name)
                    return 1
                artist_mid = candidates[0]["artist_mid"]
                logger.info(
                    "Resolved artist name '{}' -> {} ({})",
                    artist_name,
                    candidates[0]["name"],
                    artist_mid,
                )

            snapshots_dir = Path(args.snapshots_dir)
            snapshots_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            snapshot_file = snapshots_dir / "kugou_{}_{}.db".format(artist_mid, ts)
            snapshot_db_url = "sqlite:///{}".format(snapshot_file.as_posix())

            snap_client, snap_service = build_snapshot_service(database_url=snapshot_db_url)
            try:
                snap_service.crawl_songs_for_artists(
                    artist_mids=[artist_mid],
                    song_pages=args.song_pages,
                    page_size=args.song_page_size,
                )
            finally:
                snap_client.close()

            result = track_changes_for_artist(
                snapshots_dir=snapshots_dir,
                current_snapshot_file=snapshot_file,
                changes_db_file=Path(args.changes_db),
                artist_mid=artist_mid,
            )
            logger.info(
                "Change tracking done: metric_changes={}, artist_metric_changes={}",
                result["metric_changes"],
                result["artist_metric_changes"],
            )
        elif args.command == "report-changes":
            date_str = (args.date or "").strip() or None
            month_str = (args.month or "").strip() or None
            year_str = (args.year or "").strip() or None
            if sum(1 for x in (date_str, month_str, year_str) if x) > 1:
                logger.error("Use only one of --date / --month / --year")
                return 1
            if not date_str and not month_str and not year_str:
                date_str = datetime.now().strftime("%Y-%m-%d")
            report = report_changes(
                changes_db_file=Path(args.changes_db),
                date_str=date_str,
                month_str=month_str,
                year_str=year_str,
                artist_mid=(args.artist_mid or "").strip() or None,
                limit=args.limit,
            )
            logger.info(
                "Report filter(date={}, month={}, year={}), metric_changes={}, artist_metric_changes={}",
                date_str or "",
                month_str or "",
                year_str or "",
                len(report["metric_changes"]),
                len(report["artist_metric_changes"]),
            )
        elif args.command == "check-artist-toplist":
            artist_mid = (args.artist_mid or "").strip()
            artist_name = (args.artist_name or "").strip()
            if not artist_mid:
                if not artist_name:
                    logger.error("check-artist-toplist requires --artist-mid or --artist-name")
                    return 1
                candidates = service.find_artist_candidates_by_name(
                    keyword=artist_name,
                    max_pages=8,
                    page_size=settings.qqmusic_default_artist_page_size,
                )
                if not candidates:
                    logger.error("No artist matched by name: {}", artist_name)
                    return 1
                artist_mid = candidates[0]["artist_mid"]
                artist_name = candidates[0]["name"]
            if not artist_name:
                profile = client.fetch_artist_profile(artist_mid)
                artist_name = str(profile.get("name") or "").strip() or artist_mid

            hits = service.find_artist_toplist_hits(artist_mid=artist_mid, top_n=args.top_n)
            upsert_artist_toplist_hits(
                db_file=Path(args.toplist_db),
                artist_mid=artist_mid,
                artist_name=artist_name,
                hits=hits,
            )
            rows = query_artist_toplist_hits(
                db_file=Path(args.toplist_db), artist_mid=artist_mid, limit=args.limit
            )
            logger.info(
                "Toplist check done: artist={} ({}), hits={}, total_rows={}",
                artist_name,
                artist_mid,
                len(hits),
                len(rows),
            )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
