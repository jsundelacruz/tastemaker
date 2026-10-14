"""
Supabase database client for the visual research archive.
Handles image storage, embeddings, and collection management.
"""

import os
from typing import Any, Optional
from datetime import datetime

from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()


def get_supabase_client() -> Client:
    """Create Supabase client from environment variables."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise ValueError(
            "Missing SUPABASE_URL or SUPABASE_SERVICE_KEY/SUPABASE_KEY. "
            "Set these in .env or environment."
        )
    return create_client(url, key)


def insert_image(
    client: Client,
    *,
    source_platform: str,
    source_collection: Optional[str] = None,
    image_url: str,
    local_path: Optional[str] = None,
    original_title: Optional[str] = None,
    original_description: Optional[str] = None,
    original_metadata: Optional[dict] = None,
    embedding: Optional[list[float]] = None,
    date_saved: Optional[datetime] = None,
    **kwargs: Any,
) -> dict:
    """Insert a single image record."""
    data = {
        "source_platform": source_platform,
        "source_collection": source_collection,
        "image_url": image_url,
        "local_path": local_path,
        "original_title": original_title,
        "original_description": original_description,
        "original_metadata": original_metadata or {},
        "embedding": embedding,
        "date_saved": date_saved.isoformat() if date_saved else None,
        **kwargs,
    }
    data = {k: v for k, v in data.items() if v is not None}
    result = client.table("images").insert(data).execute()
    return result.data[0]


def delete_images_for_collection(client: Client, collection_name: str, source_platform: str) -> None:
    """Remove all images for a collection (so we can replace with fresh ingest, avoiding duplicates)."""
    client.table("images").delete().eq("source_collection", collection_name).eq("source_platform", source_platform).execute()


def insert_images_batch(client: Client, records: list[dict]) -> list[dict]:
    """Insert multiple image records in a single batch."""
    if not records:
        return []
    result = client.table("images").insert(records).execute()
    return result.data


def upsert_collection(
    client: Client,
    *,
    collection_name: str,
    source_platform: str,
    total_images: int = 0,
    material_vocabulary: Optional[list] = None,
    spatial_qualities: Optional[list] = None,
    recurring_forms: Optional[list] = None,
    color_palette: Optional[list] = None,
    mood_atmosphere: Optional[str] = None,
    installation_directions: Optional[list] = None,
    **kwargs: Any,
) -> dict:
    """Insert or update a collection record."""
    data = {
        "collection_name": collection_name,
        "source_platform": source_platform,
        "total_images": total_images,
        "material_vocabulary": material_vocabulary or [],
        "spatial_qualities": spatial_qualities or [],
        "recurring_forms": recurring_forms or [],
        "color_palette": color_palette or [],
        "mood_atmosphere": mood_atmosphere,
        "installation_directions": installation_directions or [],
        **kwargs,
    }
    data = {k: v for k, v in data.items() if v is not None}
    result = client.table("collections").upsert(
        data,
        on_conflict="collection_name,source_platform",
    ).execute()
    return result.data[0] if result.data else data


def list_collections(client: Client, limit: int = 50) -> list[dict]:
    """List all collections, most recently updated first."""
    result = (
        client.table("collections")
        .select("*")
        .order("updated_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


def get_sample_images(
    client: Client,
    collection_name: str,
    source_platform: str,
    limit: int = 6,
) -> list[dict]:
    """Get sample image URLs for a collection (for thumbnails on analyses page)."""
    result = (
        client.table("images")
        .select("image_url, original_metadata, local_path")
        .eq("source_collection", collection_name)
        .eq("source_platform", source_platform)
        .limit(limit)
        .execute()
    )
    return result.data or []


def list_images(client: Client, page_size: int = 1000) -> list[dict]:
    """All images for curation, paged past PostgREST's per-request row limit."""
    rows: list[dict] = []
    start = 0
    while True:
        result = (
            client.table("images")
            .select("id, image_url, source_collection, local_path")
            .order("created_at")
            .range(start, start + page_size - 1)
            .execute()
        )
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            return rows
        start += page_size


def delete_images_by_id(client: Client, image_ids: list[str]) -> None:
    """Delete images by id."""
    client.table("images").delete().in_("id", image_ids).execute()


def get_images_metadata(client: Client, image_ids: list[str]) -> dict[str, dict]:
    """Map image id -> original_metadata (for linking search results back to their source)."""
    if not image_ids:
        return {}
    result = (
        client.table("images")
        .select("id, original_metadata")
        .in_("id", image_ids)
        .execute()
    )
    return {str(r["id"]): r.get("original_metadata") or {} for r in (result.data or [])}


def get_collection_by_id(client: Client, collection_id: str) -> Optional[dict]:
    """Get a single collection by UUID."""
    result = (
        client.table("collections")
        .select("*")
        .eq("id", collection_id)
        .maybe_single()
        .execute()
    )
    return result.data


def delete_collection_and_images(client: Client, collection_id: str) -> None:
    """Delete a collection and all its images. Uses collection id."""
    coll = get_collection_by_id(client, collection_id)
    if not coll:
        return
    name = coll.get("collection_name") or ""
    platform = coll.get("source_platform") or ""
    client.table("images").delete().eq("source_collection", name).eq("source_platform", platform).execute()
    client.table("collections").delete().eq("id", collection_id).execute()


def update_collection_name(
    client: Client,
    collection_id: str,
    new_name: str,
) -> dict:
    """Update collection display name and sync source_collection on all its images."""
    coll = get_collection_by_id(client, collection_id)
    if not coll:
        raise ValueError("Collection not found")
    old_name = coll.get("collection_name") or ""
    platform = coll.get("source_platform") or ""
    new_name = (new_name or "").strip() or old_name
    client.table("collections").update({"collection_name": new_name}).eq("id", collection_id).execute()
    client.table("images").update({"source_collection": new_name}).eq("source_collection", old_name).eq("source_platform", platform).execute()
    return {**coll, "collection_name": new_name}


USER_TASTE_ID = "default"


def get_user_taste(client: Client) -> Optional[dict]:
    """Get the stored overall taste (single row, id=default)."""
    result = (
        client.table("user_taste")
        .select("content, updated_at")
        .eq("id", USER_TASTE_ID)
        .limit(1)
        .execute()
    )
    if not result.data or not isinstance(result.data, list) or len(result.data) == 0:
        return None
    row = result.data[0]
    return row if isinstance(row, dict) else None


def set_user_taste(client: Client, content: str) -> dict:
    """Store or update the overall taste. Upserts the single row."""
    from datetime import datetime
    data = {"id": USER_TASTE_ID, "content": content, "updated_at": datetime.utcnow().isoformat()}
    result = client.table("user_taste").upsert(data, on_conflict="id").execute()
    return result.data[0] if result.data else data


def get_latest_collection_updated_at(client: Client) -> Optional[str]:
    """Return the most recent collection updated_at (for needs_reanalysis check)."""
    result = (
        client.table("collections")
        .select("updated_at")
        .order("updated_at", desc=True)
        .limit(1)
        .execute()
    )
    if result.data and result.data[0].get("updated_at"):
        return result.data[0]["updated_at"]
    return None


def search_similar_images(
    client: Client,
    embedding: list[float],
    limit: int = 10,
    source_platform: Optional[str] = None,
) -> list[dict]:
    """Find images similar to the given embedding using cosine similarity."""
    query = (
        client.rpc(
            "match_images",
            {"query_embedding": embedding, "match_threshold": 0.0, "match_count": limit},
        )
        .execute()
    )
    return query.data or []