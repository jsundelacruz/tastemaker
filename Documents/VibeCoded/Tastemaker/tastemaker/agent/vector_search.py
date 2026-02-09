"""Vector search for similar images."""

from database.db_client import get_supabase_client, search_similar_images


def find_similar(
    embedding: list[float],
    limit: int = 10,
    source_platform: str | None = None,
) -> list[dict]:
    """Find images similar to the given embedding."""
    client = get_supabase_client()
    return search_similar_images(
        client, embedding=embedding, limit=limit, source_platform=source_platform
    )
