#!/usr/bin/env python3
"""
Ingest Instagram saved posts from a filtered export JSON (no Apify).

Reads saved_posts_filtered.json, fetches each post URL, extracts og:image from HTML,
uploads images to Supabase Storage, generates CLIP embeddings, and inserts into images table.

Usage (from tastemaker/ directory):
  python -m ingestion.ingest_instagram_direct path/to/saved_posts_filtered.json
  python -m ingestion.ingest_instagram_direct path/to/saved_posts_filtered.json --limit 100
  python ingestion/ingest_instagram_direct.py path/to/saved_posts_filtered.json --limit 50
"""

import argparse
import json
import os
import random
import re
import sys
import time
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
from supabase import create_client

# Project root (tastemaker/) for imports
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingestion.generate_embeddings import encode_image

load_dotenv(ROOT / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
BUCKET = "images"

# Realistic browser User-Agent for fetching Instagram pages
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def load_filtered_json(path: str) -> list:
    """Load saved_posts_filtered.json; return list of post items."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("saved_posts", "posts", "data", "items", "results"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _normalize_timestamp(ts) -> Optional[str]:
    """Convert export timestamp to ISO string for date_saved."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        from datetime import datetime
        try:
            if ts > 1e12:
                ts = ts / 1000.0
            return datetime.utcfromtimestamp(ts).isoformat() + "Z"
        except (ValueError, OSError):
            return None
    s = str(ts).strip()
    if not s:
        return None
    if "T" in s or re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s
    return None


def extract_post_info(item: dict) -> Optional[dict]:
    """
    Extract post_url, username, post_id, caption, and date_saved from a filtered export item.
    Post URL from string_map_data["Saved on"]["href"], username from title.
    Caption and timestamp from string_map_data when present.
    """
    try:
        string_map = item.get("string_map_data") or item.get("string_map_data_data") or {}
        saved_on = string_map.get("Saved on") or {}
        if isinstance(saved_on, dict):
            href = saved_on.get("href") or saved_on.get("value")
            timestamp = saved_on.get("timestamp") or saved_on.get("Timestamp")
        else:
            href = None
            timestamp = None
        if not href or not isinstance(href, str) or "instagram.com" not in href:
            return None

        title = item.get("title") or ""
        username = "unknown"
        if title:
            username = title.replace("'s post", "").replace("'s Post", "").strip() or "unknown"
        username = re.sub(r"[^\w.-]", "_", username)[:64] or "unknown"

        parsed = urlparse(href)
        path = (parsed.path or "").strip("/")
        parts = path.split("/")
        post_id = None
        if len(parts) >= 2 and parts[0] in ("p", "reel"):
            post_id = parts[1]
        if not post_id:
            post_id = str(hash(href) % (10 ** 10))

        caption = None
        cap_entry = string_map.get("Caption") or string_map.get("caption")
        if isinstance(cap_entry, dict):
            caption = cap_entry.get("value") or cap_entry.get("href") or cap_entry.get("text")
        elif isinstance(cap_entry, str):
            caption = cap_entry
        if not caption:
            caption = item.get("caption") or item.get("Caption") or item.get("media_caption")
        if isinstance(caption, list):
            caption = (caption[0] if caption else None) or None
        if caption and len(caption) > 5000:
            caption = caption[:5000]

        date_saved = _normalize_timestamp(timestamp)
        if not date_saved and isinstance(item.get("timestamp"), (int, float)):
            date_saved = _normalize_timestamp(item["timestamp"])
        if not date_saved and isinstance(item.get("taken_at"), (int, float)):
            date_saved = _normalize_timestamp(item["taken_at"])

        return {
            "post_url": href,
            "username": username,
            "post_id": post_id,
            "caption": caption,
            "date_saved": date_saved,
        }
    except Exception:
        return None


def fetch_og_image(post_url: str, timeout: int = 15) -> Optional[str]:
    """Fetch post URL, parse HTML with BeautifulSoup, return og:image content URL."""
    try:
        resp = requests.get(
            post_url,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            allow_redirects=True,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        meta = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
        if meta and meta.get("content"):
            return meta["content"].strip()
        return None
    except Exception:
        return None


def download_image(url: str, dest_path: Path, timeout: int = 30) -> bool:
    """Download image from url to dest_path. Returns True on success."""
    try:
        r = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            allow_redirects=True,
        )
        r.raise_for_status()
        dest_path.write_bytes(r.content)
        return True
    except Exception:
        return False


def storage_path_for_post(username: str, post_id: str) -> str:
    """Return Supabase storage path: instagram/{username}/{post_id}.jpg"""
    safe_user = re.sub(r"[^\w.-]", "_", (username or "unknown")[:64])
    safe_id = re.sub(r"[^\w.-]", "_", str(post_id)[:64])
    return f"instagram/{safe_user}/{safe_id}.jpg"


def upload_to_supabase_storage(client, local_path: Path, storage_path: str) -> Optional[str]:
    """Upload file to bucket 'images'. Returns public URL on success."""
    try:
        bucket = client.storage.from_(BUCKET)
        with open(local_path, "rb") as f:
            bucket.upload(storage_path, f.read(), file_options={"content-type": "image/jpeg"})
        base = SUPABASE_URL.rstrip("/")
        return f"{base}/storage/v1/object/public/{BUCKET}/{storage_path}"
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Ingest Instagram saved posts from filtered export JSON (no Apify)"
    )
    parser.add_argument("json_path", help="Path to saved_posts_filtered.json")
    parser.add_argument("--limit", type=int, default=300, help="Max number of posts to process (default: 300)")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: Set SUPABASE_URL and SUPABASE_KEY (or SUPABASE_SERVICE_KEY) in .env", file=sys.stderr)
        sys.exit(1)

    json_path = Path(args.json_path)
    if not json_path.exists():
        print(f"Error: File not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    items = load_filtered_json(str(json_path))
    total = min(len(items), max(1, args.limit))
    to_process = items[:total]
    print(f"Loaded {len(items)} items from {json_path}, processing up to {total}")

    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    succeeded = 0
    failed = 0

    for i, item in enumerate(to_process):
        n = i + 1
        info = extract_post_info(item)
        if not info:
            print(f"[{n}/{total}] ? — failed (no post URL or username)")
            failed += 1
            continue

        post_url = info["post_url"]
        username = info["username"]
        post_id = info["post_id"]
        caption = info.get("caption")
        date_saved = info.get("date_saved")

        # Rate limit: 2–3 s delay before each request (before fetching the page)
        if i > 0:
            time.sleep(random.uniform(2.0, 3.0))

        og_url = fetch_og_image(post_url)
        if not og_url:
            print(f"[{n}/{total}] {username} — failed (no og:image)")
            failed += 1
            continue

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            if not download_image(og_url, tmp_path):
                print(f"[{n}/{total}] {username} — failed (image download)")
                failed += 1
                continue

            storage_path = storage_path_for_post(username, post_id)
            public_url = upload_to_supabase_storage(client, tmp_path, storage_path)
            if not public_url:
                print(f"[{n}/{total}] {username} — failed (upload)")
                failed += 1
                continue

            embedding = encode_image(tmp_path.read_bytes())
        except Exception as e:
            print(f"[{n}/{total}] {username} — failed ({e})")
            failed += 1
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            continue

        try:
            row = {
                "source_platform": "instagram",
                "source_collection": username,
                "image_url": public_url,
                "local_path": storage_path,
                "original_description": caption,
                "original_metadata": {
                    "username": username,
                    "post_url": post_url,
                    "post_id": post_id,
                },
                "date_saved": date_saved,
                "embedding": embedding,
            }
            client.table("images").insert(row).execute()
            print(f"[{n}/{total}] {username} — ok")
            succeeded += 1
        except Exception as e:
            print(f"[{n}/{total}] {username} — failed ({e})")
            failed += 1
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    print()
    print("Summary:")
    print(f"  Total processed: {total}")
    print(f"  Succeeded:       {succeeded}")
    print(f"  Failed:         {failed}")


if __name__ == "__main__":
    main()
