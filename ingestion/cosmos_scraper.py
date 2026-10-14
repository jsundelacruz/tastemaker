"""
Cosmos.so cluster scraper.
Fetches a public cluster page and extracts image/element URLs for ingestion.
"""

import re
from typing import Optional
from urllib.parse import urlparse

import httpx


def _cluster_slug(url: str) -> str:
    """Extract cluster slug from URL (e.g. cosmos.so/user/cluster-name -> cluster-name)."""
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    if len(parts) >= 2:
        return parts[-1]
    return parts[0] if parts else "cluster"


def _extract_images_from_next_data(html: str) -> list[dict]:
    """Try to extract element/image data from __NEXT_DATA__ script."""
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>([^<]+)</script>', html, re.DOTALL)
    if not match:
        return []

    try:
        import json
        data = json.loads(match.group(1))
        props = (data.get("props") or {}).get("pageProps") or {}
        elements = props.get("elements") or props.get("cluster", {}).get("elements") or []
        if not elements and "cluster" in props:
            cluster = props["cluster"]
            if isinstance(cluster, dict):
                elements = cluster.get("elements") or []
        records = []
        seen = set()
        for el in elements:
            if not isinstance(el, dict):
                continue
            # Various possible shapes: { image: { url } }, { url }, { src }, { imageUrl }
            url = (
                (el.get("image") or {}).get("url") if isinstance(el.get("image"), dict) else None
            ) or el.get("imageUrl") or el.get("url") or el.get("src") or (el.get("media") or {}).get("url")
            if not url or not url.startswith("http"):
                continue
            if url in seen:
                continue
            seen.add(url)
            title = el.get("title") or el.get("caption") or el.get("name") or ""
            records.append({"image_url": url, "title": title})
        return records
    except Exception:
        return []


def _extract_images_from_html(html: str) -> list[str]:
    """Fallback: find cdn.cosmos.so image URLs in HTML."""
    # Match cdn.cosmos.so/... in src, data-src, or href
    pattern = r'https?://cdn\.cosmos\.so/[^\s"\'<>?]+'
    urls = list(dict.fromkeys(re.findall(pattern, html)))
    # Prefer larger images: drop thumbnails with w=100 or small dimensions if we have others
    return urls


def scrape_cosmos_cluster(
    cluster_url: str,
    *,
    max_elements: int = 30,
    timeout: float = 15,
) -> list[dict]:
    """
    Scrape a Cosmos cluster page and return image records for the research archive.

    Args:
        cluster_url: Full cluster URL (e.g. https://cosmos.so/username/cluster-name)
        max_elements: Maximum number of images to return
        timeout: Request timeout in seconds

    Returns:
        List of dicts with image_url, original_title, source_collection, source_platform, etc.
    """
    if "cosmos.so" not in cluster_url:
        raise ValueError("Invalid Cosmos URL")

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        resp = client.get(cluster_url, headers=headers)
        resp.raise_for_status()
        html = resp.text

    source_collection = _cluster_slug(cluster_url)
    records_from_next = _extract_images_from_next_data(html)
    if records_from_next:
        return [
            {
                "source_platform": "cosmos",
                "source_collection": source_collection,
                "image_url": r["image_url"],
                "original_title": r.get("title") or "",
                "original_description": None,
                "original_metadata": {"cluster_url": cluster_url},
            }
            for r in records_from_next[:max_elements]
        ]

    urls = _extract_images_from_html(html)[:max_elements]
    return [
        {
            "source_platform": "cosmos",
            "source_collection": source_collection,
            "image_url": url,
            "original_title": "",
            "original_description": None,
            "original_metadata": {"cluster_url": cluster_url},
        }
        for url in urls
    ]
