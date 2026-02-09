"""
Pinterest board scraper using Apify.
Scrapes pins from a Pinterest board URL and returns standardized image records.
"""

import os
from typing import Optional
from datetime import datetime

import httpx
from apify_client import ApifyClient
from dotenv import load_dotenv

load_dotenv()


def _resolve_pin_it(url: str) -> str:
    """Resolve pin.it short link to full Pinterest URL."""
    if "pin.it" not in url:
        return url
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=10)
        return str(resp.url)
    except Exception:
        return url

# Configure via APIFY_PINTEREST_ACTOR env var.
# headlessagent/pinterest-board-downloader: pay-per-use ($2.50/1K), no rental
# red.cars/pinterest-board-scraper: requires rental (paid Actor)
DEFAULT_ACTOR_ID = "headlessagent/pinterest-board-downloader"


def scrape_pinterest_board(
    board_url: str,
    *,
    max_pins: int = 50,
    actor_id: Optional[str] = None,
    apify_token: Optional[str] = None,
) -> list[dict]:
    """
    Scrape a Pinterest board and return image records for the research archive.

    Args:
        board_url: Full Pinterest board URL (e.g. https://www.pinterest.com/username/board-name/)
        max_pins: Maximum number of pins to scrape
        actor_id: Apify actor ID (default: compass/pinterest-board-scraper)
        apify_token: Apify API token (or APIFY_TOKEN env var)

    Returns:
        List of dicts with keys: image_url, original_title, original_description,
        original_metadata, source_collection, date_saved
    """
    token = apify_token or os.getenv("APIFY_TOKEN")
    if not token:
        raise ValueError("APIFY_TOKEN required. Set in .env or pass apify_token.")

    board_url = _resolve_pin_it(board_url)
    client = ApifyClient(token)
    actor = actor_id or os.getenv("APIFY_PINTEREST_ACTOR", DEFAULT_ACTOR_ID)

    # Input format varies by actor
    if "headlessagent/pinterest-board-downloader" in actor:
        run_input = {"url": board_url}
    else:
        run_input = {"boardUrls": [board_url], "maxPins": max_pins}

    run = client.actor(actor).call(run_input=run_input)
    dataset_id = run["defaultDatasetId"]
    items = list(client.dataset(dataset_id).iterate_items())

    # Normalize output to our schema (handles different actor output formats)
    records = []
    for item in items:
        record = _normalize_pin(item, board_url)
        if record:
            records.append(record)
        if len(records) >= max_pins:
            break

    return records


def _extract_image_url(raw: dict) -> Optional[str]:
    """Extract image URL from raw pin data (handles various Apify output formats)."""
    image_url = (
        raw.get("imageUrl") or raw.get("image") or raw.get("image_url")
        or raw.get("mediaUrl") or raw.get("orig")
    )
    if image_url:
        return image_url

    if isinstance(raw.get("images"), dict):
        return raw["images"].get("orig") or raw["images"].get("url")

    if isinstance(raw.get("images"), list) and raw["images"]:
        img_list = raw["images"]
        fallback_url = None
        for img in img_list:
            if isinstance(img, dict) and img.get("url"):
                url = img["url"]
                if img.get("quality") == "orig":
                    return url
                fallback_url = fallback_url or url
            elif isinstance(img, str) and img.startswith("http"):
                fallback_url = fallback_url or img
        return fallback_url

    if isinstance(raw.get("image_urls"), list) and raw["image_urls"]:
        return raw["image_urls"][0]

    # Fallback: search for pinimg.com URL anywhere in the item
    def _find_pinimg_url(obj, depth=0):
        if depth > 5:
            return None
        if isinstance(obj, str) and "pinimg.com" in obj:
            return obj
        if isinstance(obj, dict):
            for v in obj.values():
                found = _find_pinimg_url(v, depth + 1)
                if found:
                    return found
        if isinstance(obj, list):
            for v in obj:
                found = _find_pinimg_url(v, depth + 1)
                if found:
                    return found
        return None

    return _find_pinimg_url(raw)


def _normalize_pin(raw: dict, board_url: str) -> Optional[dict]:
    """Normalize raw Apify output to our images table schema."""
    image_url = _extract_image_url(raw)
    if not image_url:
        return None

    # Extract board/collection name from URL or raw data
    board = raw.get("board") or {}
    source_collection = (
        raw.get("boardName") or (board.get("name") if isinstance(board, dict) else None)
        or _extract_board_name(board_url)
    )

    return {
        "source_platform": "pinterest",
        "source_collection": source_collection,
        "image_url": image_url,
        "original_title": raw.get("title") or raw.get("pinTitle"),
        "original_description": raw.get("description") or raw.get("pinDescription"),
        "original_metadata": {
            "pin_id": raw.get("id") or raw.get("pinId"),
            "pin_url": raw.get("url") or raw.get("pinUrl"),
            "board_url": board_url,
        },
        "date_saved": None,  # Pinterest doesn't expose pin save date via scrape
    }


def _extract_board_name(url: str) -> str:
    """Extract board name from Pinterest URL (e.g. /user/board-name/ -> board-name)."""
    parts = url.rstrip("/").split("/")
    return parts[-1] if parts else "unknown"
