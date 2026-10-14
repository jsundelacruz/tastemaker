#!/usr/bin/env python3
"""
Ingest Apify Instagram scraper output (v2) into TasteMaker.

Reads a top-level array JSON. For each item: displayUrl, ownerUsername, shortCode,
url, caption, timestamp. Downloads image, uploads to Supabase Storage, generates
CLIP embedding, inserts into images table.

Usage (from tastemaker/ directory):
  python -m ingestion.ingest_apify_instagram_v2 path/to/apify_output.json
  python ingestion/ingest_apify_instagram_v2.py path/to/apify_output.json
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

from dotenv import load_dotenv
import httpx
from supabase import create_client

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingestion.generate_embeddings import encode_image

load_dotenv(ROOT / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
BUCKET = "images"


def _normalize_timestamp(ts) -> Optional[str]:
    """Convert timestamp to ISO string for date_saved."""
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


def load_apify_json(path: str) -> list:
    """Load Apify Instagram output; return list of items (top-level array or wrapper)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("results", "items", "data", "dataset"):
            if isinstance(data.get(key), list):
                return data[key]
        if data.get("displayUrl") or data.get("display_url"):
            return [data]
    return []


def storage_path_for_post(username: str, short_code: str) -> str:
    """Return Supabase storage path: instagram/{ownerUsername}/{shortCode}.jpg"""
    safe_user = re.sub(r"[^\w.-]", "_", (username or "unknown")[:64])
    safe_id = re.sub(r"[^\w.-]", "_", str(short_code)[:64])
    return f"instagram/{safe_user}/{safe_id}.jpg"


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
        description="Ingest Apify Instagram scraper output (v2) into TasteMaker"
    )
    parser.add_argument("json_path", help="Path to Apify Instagram scraper output JSON")
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

    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    succeeded = 0
    failed = 0

    for i, item in enumerate(items):
        n = i + 1
        display_url = (item.get("displayUrl") or item.get("display_url") or "").strip()
        if not display_url:
            print(f"[{n}/{total}] ? — skipped (no displayUrl)")
            failed += 1
            continue

        owner_username = item.get("ownerUsername") or item.get("owner_username") or "unknown"
        if isinstance(owner_username, dict):
            owner_username = owner_username.get("username") or "unknown"
        short_code = item.get("shortCode") or item.get("short_code") or ""
        post_url = item.get("url") or item.get("postUrl") or item.get("post_url") or ""
        caption = item.get("caption") or item.get("captionText") or None
        if isinstance(caption, list):
            caption = (caption[0] if caption else None) or None
        if caption and len(caption) > 5000:
            caption = caption[:5000]
        timestamp = item.get("timestamp") or item.get("takenAt") or item.get("taken_at")
        date_saved = _normalize_timestamp(timestamp)

        if not short_code:
            short_code = str(hash(display_url) % (10 ** 10))

        if i > 0:
            time.sleep(random.uniform(1.0, 2.0))

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            if not download_image(display_url, tmp_path):
                print(f"[{n}/{total}] {owner_username} — failed (download)")
                failed += 1
                continue

            storage_path = storage_path_for_post(owner_username, short_code)
            public_url = upload_to_supabase_storage(client, tmp_path, storage_path)
            if not public_url:
                print(f"[{n}/{total}] {owner_username} — failed (upload)")
                failed += 1
                continue

            embedding = encode_image(tmp_path.read_bytes())
        except Exception as e:
            print(f"[{n}/{total}] {owner_username} — failed ({e})")
            failed += 1
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            continue

        try:
            row = {
                "source_platform": "instagram",
                "source_collection": owner_username,
                "image_url": public_url,
                "local_path": storage_path,
                "original_description": caption,
                "original_metadata": {
                    "username": owner_username,
                    "post_url": post_url,
                    "post_id": short_code,
                },
                "date_saved": date_saved,
                "embedding": embedding,
            }
            client.table("images").insert(row).execute()
            print(f"[{n}/{total}] {owner_username} — ok")
            succeeded += 1
        except Exception as e:
            print(f"[{n}/{total}] {owner_username} — failed ({e})")
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
