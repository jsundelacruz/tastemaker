#!/usr/bin/env python3
"""
Ingest Instagram posts into TasteMaker.

Accepts either input format (detected automatically, or forced with --format):
  apify   Apify Instagram scraper output: items with displayUrl, ownerUsername,
          shortCode, url, caption, timestamp.
  export  Instagram data export of saved posts (e.g. saved_posts_filtered.json):
          items with string_map_data["Saved on"].href. The image is read from each
          post page's og:image, so no Apify run is needed.

For each post: download the image, upload it to Supabase Storage
(instagram/{username}/{post_id}.jpg), generate a CLIP embedding, and insert a row
into images. Posts already in the archive (same post URL) are skipped, so re-runs
only add what's new.

Usage (from tastemaker/ directory):
  python -m ingestion.ingest_instagram path/to/posts.json
  python -m ingestion.ingest_instagram path/to/posts.json --limit 50 --dry-run
"""

import argparse
import hashlib
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

BUCKET = "images"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
# Seconds to wait between posts. Export mode fetches instagram.com pages, so go slower there.
DELAYS = {"apify": (1.0, 2.0), "export": (2.0, 3.0)}


def load_items(path: Path) -> list:
    """Load a top-level array, or the first list found under a common wrapper key."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("saved_posts", "posts", "results", "items", "data", "dataset"):
            if isinstance(data.get(key), list):
                return data[key]
        if data.get("displayUrl") or data.get("display_url"):
            return [data]
    return []


def detect_format(items: list) -> Optional[str]:
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        if item.get("displayUrl") or item.get("display_url"):
            return "apify"
        if item.get("string_map_data") or item.get("string_map_data_data"):
            return "export"
    return None


def _normalize_timestamp(ts) -> Optional[str]:
    """Convert an epoch (s or ms) or ISO-ish string to an ISO timestamp for date_saved."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        try:
            if ts > 1e12:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except (ValueError, OSError):
            return None
    s = str(ts).strip()
    if "T" in s or re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s
    return None


def _post_id(post_url: str, fallback_key: str) -> str:
    """Shortcode from /p/<id>/ or /reel/<id>/; otherwise a stable hash (same input, same id on every run)."""
    parts = (urlparse(post_url or "").path or "").strip("/").split("/")
    if len(parts) >= 2 and parts[0] in ("p", "reel"):
        return parts[1]
    return hashlib.sha1((post_url or fallback_key).encode()).hexdigest()[:12]


def _clean_caption(caption) -> Optional[str]:
    if isinstance(caption, list):
        caption = caption[0] if caption else None
    return caption[:5000] if isinstance(caption, str) and caption else None


def parse_apify_item(item: dict) -> Optional[dict]:
    image_url = (item.get("displayUrl") or item.get("display_url") or "").strip()
    if not image_url:
        return None
    username = item.get("ownerUsername") or item.get("owner_username") or "unknown"
    if isinstance(username, dict):
        username = username.get("username") or "unknown"
    post_url = item.get("url") or item.get("postUrl") or item.get("post_url") or ""
    post_id = item.get("shortCode") or item.get("short_code") or _post_id(post_url, image_url)
    return {
        "username": username,
        "post_url": post_url,
        "post_id": post_id,
        "image_url": image_url,
        "caption": _clean_caption(item.get("caption") or item.get("captionText")),
        "date_saved": _normalize_timestamp(item.get("timestamp") or item.get("takenAt") or item.get("taken_at")),
    }


def parse_export_item(item: dict) -> Optional[dict]:
    """Saved-post export item: URL from string_map_data["Saved on"].href, username from title."""
    string_map = item.get("string_map_data") or item.get("string_map_data_data") or {}
    saved_on = string_map.get("Saved on") or {}
    if not isinstance(saved_on, dict):
        return None
    post_url = saved_on.get("href") or saved_on.get("value")
    if not isinstance(post_url, str) or "instagram.com" not in post_url:
        return None

    title = item.get("title") or ""
    username = title.replace("'s post", "").replace("'s Post", "").strip() or "unknown"
    username = re.sub(r"[^\w.-]", "_", username)[:64] or "unknown"

    post_id = _post_id(post_url, post_url)

    cap_entry = string_map.get("Caption") or string_map.get("caption")
    caption = cap_entry.get("value") or cap_entry.get("text") if isinstance(cap_entry, dict) else cap_entry
    caption = caption or item.get("caption") or item.get("Caption") or item.get("media_caption")

    date_saved = (
        _normalize_timestamp(saved_on.get("timestamp") or saved_on.get("Timestamp"))
        or _normalize_timestamp(item.get("timestamp"))
        or _normalize_timestamp(item.get("taken_at"))
    )
    return {
        "username": username,
        "post_url": post_url,
        "post_id": post_id,
        "image_url": None,  # resolved from the post page's og:image
        "caption": _clean_caption(caption),
        "date_saved": date_saved,
    }


PARSERS = {"apify": parse_apify_item, "export": parse_export_item}


def fetch_og_image(http: httpx.Client, post_url: str) -> Optional[str]:
    resp = http.get(post_url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    meta = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
    return meta["content"].strip() if meta and meta.get("content") else None


def storage_path_for_post(username: str, post_id: str) -> str:
    safe_user = re.sub(r"[^\w.-]", "_", (username or "unknown")[:64])
    safe_id = re.sub(r"[^\w.-]", "_", str(post_id)[:64])
    return f"instagram/{safe_user}/{safe_id}.jpg"


def existing_post_urls(client) -> set[str]:
    """Post URLs already in the archive, so re-runs skip them."""
    urls: set[str] = set()
    start, page_size = 0, 1000
    while True:
        rows = (
            client.table("images")
            .select("original_metadata")
            .eq("source_platform", "instagram")
            .range(start, start + page_size - 1)
            .execute()
        ).data or []
        urls.update((r.get("original_metadata") or {}).get("post_url") for r in rows)
        if len(rows) < page_size:
            urls.discard(None)
            return urls
        start += page_size


def ingest_post(client, http: httpx.Client, post: dict) -> None:
    """Download, store, embed and insert one post. Raises with a short reason on failure."""
    from ingestion.generate_embeddings import encode_image

    image_url = post["image_url"] or fetch_og_image(http, post["post_url"])
    if not image_url:
        raise ValueError("no og:image on post page")
    resp = http.get(image_url)
    resp.raise_for_status()
    image_bytes = resp.content

    embedding = encode_image(image_bytes)  # before upload, so a bad image leaves nothing behind
    storage_path = storage_path_for_post(post["username"], post["post_id"])
    client.storage.from_(BUCKET).upload(
        storage_path, image_bytes, file_options={"content-type": "image/jpeg", "upsert": "true"}
    )
    public_url = client.storage.from_(BUCKET).get_public_url(storage_path).rstrip("?")

    client.table("images").insert({
        "source_platform": "instagram",
        "source_collection": post["username"],
        "image_url": public_url,
        "local_path": storage_path,
        "original_description": post["caption"],
        "original_metadata": {
            "username": post["username"],
            "post_url": post["post_url"],
            "post_id": post["post_id"],
        },
        "date_saved": post["date_saved"],
        "embedding": embedding,
    }).execute()


def main():
    parser = argparse.ArgumentParser(description="Ingest Instagram posts (Apify output or saved-posts export) into TasteMaker")
    parser.add_argument("json_path", type=Path, help="Apify output JSON or Instagram saved-posts export JSON")
    parser.add_argument("--format", choices=["auto", "apify", "export"], default="auto")
    parser.add_argument("--limit", type=int, default=None, help="Process at most this many new posts")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report what would be ingested; no downloads or writes")
    args = parser.parse_args()

    if not args.json_path.exists():
        sys.exit(f"Error: file not found: {args.json_path}")
    items = load_items(args.json_path)
    fmt = detect_format(items) if args.format == "auto" else args.format
    if not fmt:
        sys.exit("Error: could not detect the input format; pass --format apify or --format export")

    parsed = [PARSERS[fmt](item) for item in items if isinstance(item, dict)]
    posts = [p for p in parsed if p]
    unparseable = len(items) - len(posts)

    from database.db_client import get_supabase_client
    client = get_supabase_client()
    known = existing_post_urls(client)
    new_posts = [p for p in posts if not p["post_url"] or p["post_url"] not in known]
    already = len(posts) - len(new_posts)
    if args.limit is not None:
        new_posts = new_posts[: max(0, args.limit)]

    print(f"{args.json_path.name}: {len(items)} items ({fmt} format) · {already} already in archive · "
          f"{unparseable} unparseable · {len(new_posts)} to ingest")
    if args.dry_run:
        for p in new_posts[:10]:
            print(f"  {p['username']:<24} {p['post_url']}")
        if len(new_posts) > 10:
            print(f"  … and {len(new_posts) - 10} more")
        return

    succeeded = failed = 0
    lo, hi = DELAYS[fmt]
    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30) as http:
        for n, post in enumerate(new_posts, 1):
            if n > 1:
                time.sleep(random.uniform(lo, hi))
            try:
                ingest_post(client, http, post)
                succeeded += 1
                print(f"[{n}/{len(new_posts)}] {post['username']} — ok")
            except Exception as e:
                failed += 1
                print(f"[{n}/{len(new_posts)}] {post['username']} — failed ({e})")

    print(f"\nDone: {succeeded} ingested, {failed} failed, {already} skipped (already in archive)")


if __name__ == "__main__":
    main()
