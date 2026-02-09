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