#!/usr/bin/env python3
"""
Ingest Apify Instagram Post Scraper output into TasteMaker.

Loads JSON from Apify, downloads each post image, uploads to Supabase Storage,
generates CLIP embeddings, and inserts rows into the images table.

Usage (from tastemaker/ directory):
  python -m ingestion.ingest_apify_instagram path/to/apify_output.json
  python ingestion/ingest_apify_instagram.py path/to/apify_output.json
"""

import argparse
import json
import os
import sys
import tempfile
import re
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
import httpx
from supabase import create_client

# Project root (tastemaker/) for imports
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingestion.generate_embeddings import encode_image

load_dotenv(ROOT / ".env")

# Supabase env: script uses SUPABASE_KEY; existing app also supports SUPABASE_SERVICE_KEY
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
BUCKET = "images"


def _normalize_timestamp(ts):
    """Parse common timestamp formats to ISO for date_saved."""
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
    # Already ISO-like
    if "T" in s or re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s
    return None


def _safe_filename(url: str, post_id: str, username: str, index: int) -> str:
    """Generate a safe storage path: instagram/{username}/{post_id or index}.jpg"""
    safe_user = re.sub(r"[^\w.-]", "_", (username or "unknown")[:64])
    ext = "jpg"
    if post_id:
        safe_id = re.sub(r"[^\w.-]", "_", str(post_id)[:32])
        name = f"{safe_id}.{ext}"
    else:
        name = f"{index:06d}.{ext}"
    return f"instagram/{safe_user}/{name}"


def load_apify_json(path: str) -> list:
    """Load Apify output JSON; return list of items (handle dataset or results wrapper)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Apify often returns { "results": [...] } or dataset as array in a key
        for key in ("results", "items", "data", "dataset"):
            if isinstance(data.get(key), list):
                return data[key]
        # Single object as one post
        if data.get("displayUrl") or data.get("display_url"):
            return [data]
    return []


def get_post_fields(item: dict, index: int) -> dict:
    """Extract displayUrl, post URL, username, caption, timestamp from an item (camel or snake)."""
    display_url = item.get("displayUrl") or item.get("display_url") or item.get("imageUrl") or item.get("image_url")
    post_url = item.get("url") or item.get("postUrl") or item.get("post_url") or item.get("link") or ""
    username = (
        item.get("ownerUsername") or item.get("owner_username")
        or item.get("username") or item.get("owner") or ""
    )
    if isinstance(username, dict):
        username = username.get("username") or ""
    caption = item.get("caption") or item.get("captionText") or item.get("caption_text") or item.get("description") or ""
    if isinstance(caption, list):
        caption = (caption[0] if caption else "") or ""
    timestamp = item.get("timestamp") or item.get("takenAt") or item.get("taken_at") or item.get("created_at")
    post_id = item.get("id") or item.get("shortCode") or item.get("short_code") or ""
    return {
        "display_url": display_url,
        "post_url": post_url,
        "username": username or "unknown",
        "caption": caption[:2000] if caption else None,
        "timestamp": _normalize_timestamp(timestamp),
        "post_id": str(post_id) if post_id else None,
        "index": index,
    }


def download_image(url: str, dest_path: Path, timeout: int = 30) -> bool:
    """Download image from url to dest_path. Returns True on success."""
    try:
        r = httpx.get(url, follow_redirects=True, timeout=timeout)
        r.raise_for_status()
        dest_path.write_bytes(r.content)
        return True
    except Exception:
        return False


def upload_to_supabase_storage(client, local_path: Path, storage_path: str) -> Optional[str]:
    """
    Upload file to Supabase Storage bucket 'images'.
    Returns public URL on success, None on failure.
    """
    try:
        bucket = client.storage.from_(BUCKET)
        # supabase-py upload(path_in_bucket, file_path_or_bytes)
        with open(local_path, "rb") as f:
            bucket.upload(storage_path, f.read(), file_options={"content-type": "image/jpeg"})
        # Public URL for default public bucket
        base = SUPABASE_URL.rstrip("/")
        return f"{base}/storage/v1/object/public/{BUCKET}/{storage_path}"
    except Exception:
        return None


def ensure_bucket(client):
    """Ensure bucket exists (create if possible). Suppress errors if no permission."""
    try:
        client.storage.get_bucket(BUCKET)
    except Exception:
        try:
            client.storage.create_bucket(BUCKET, options={"public": True})
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="Ingest Apify Instagram JSON into TasteMaker")
    parser.add_argument("json_path", help="Path to Apify Instagram scraper output JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Only load JSON and print count, no download/upload/DB")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: Set SUPABASE_URL and SUPABASE_KEY (or SUPABASE_SERVICE_KEY) in .env", file=sys.stderr)
        sys.exit(1)

    json_path = Path(args.json_path)
    if not json_path.exists():
        print(f"Error: File not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    items = load_apify_json(str(json_path))
    total = len(items)
    print(f"Loaded {total} items from {json_path}")

    if args.dry_run:
        with_display = sum(1 for i, it in enumerate(items) if (get_post_fields(it, i)["display_url"]))
        print(f"Items with displayUrl: {with_display}")
        return

    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    ensure_bucket(client)

    succeeded = 0
    failed = 0

    for i, item in enumerate(items):
        n = i + 1
        fields = get_post_fields(item, i)
        display_url = fields["display_url"]
        username = fields["username"]

        if not display_url:
            print(f"[{n}/{total}] {username} — skipped (no displayUrl)")
            failed += 1
            continue

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            if not download_image(display_url, tmp_path):
                print(f"[{n}/{total}] {username} — failed (download)")
                failed += 1
                continue

            storage_path = _safe_filename(
                display_url,
                fields["post_id"],
                username,
                i,
            )
            public_url = upload_to_supabase_storage(client, tmp_path, storage_path)
            if not public_url:
                print(f"[{n}/{total}] {username} — failed (upload)")
                failed += 1
                continue

            embedding = encode_image(tmp_path.read_bytes())

            # Map to existing images table schema (source_platform, image_url, source_collection, etc.)
            row = {
                "source_platform": "instagram",
                "source_collection": username,
                "image_url": public_url,
                "local_path": storage_path,
                "original_title": (fields["caption"] or "")[:500] if fields["caption"] else None,
                "original_description": fields["caption"],
                "original_metadata": {
                    "post_url": fields["post_url"],
                    "username": username,
                    "storage_path": storage_path,
                },
                "embedding": embedding,
                "date_saved": fields["timestamp"],
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
    print(f"  Failed:          {failed}")


if __name__ == "__main__":
    main()
