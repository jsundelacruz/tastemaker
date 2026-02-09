#!/usr/bin/env python3
"""
End-to-end validation script for the visual research archive pipeline.

Only scrapes boards you explicitly select.

Run from tastemaker/ dir:
  python3 scripts/quick_test.py                    # Process boards in boards.txt
  python3 scripts/quick_test.py <BOARD_URL>        # Process single board

Environment:
  - APIFY_TOKEN: Apify API key
  - SUPABASE_URL, SUPABASE_SERVICE_KEY (or SUPABASE_KEY)
  - ANTHROPIC_API_KEY: for Claude analysis (optional for ingest-only)
"""

import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.pinterest_scraper import scrape_pinterest_board
from ingestion.generate_embeddings import add_embeddings_to_records
from database.db_client import get_supabase_client, insert_images_batch, upsert_collection


def _load_selected_boards() -> list[str]:
    """Load board URLs from boards.txt (one per line, # for comments)."""
    path = Path(__file__).resolve().parent.parent / "boards.txt"
    if not path.exists():
        return []
    urls = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def main():
    project_root = Path(__file__).resolve().parent.parent
    boards_path = project_root / "boards.txt"

    # Get boards to process: CLI arg, or from boards.txt
    if len(sys.argv) > 1:
        board_urls = [sys.argv[1]]
    else:
        board_urls = _load_selected_boards()
        if not board_urls:
            print("No boards selected.")
            print()
            print("Option 1: Add board URLs to boards.txt (one per line)")
            print(f"  Edit: {boards_path}")
            print()
            print("Option 2: Pass a board URL as argument")
            print("  python3 scripts/quick_test.py https://www.pinterest.com/username/board-name/")
            sys.exit(1)

    print("=" * 60)
    print("TasteMaker - End-to-End Pipeline Test")
    print("=" * 60)
    print(f"\nBoards to process: {len(board_urls)}")
    for url in board_urls:
        print(f"  • {url}")

    # Step 1: Scrape selected Pinterest boards
    print("\n[1/4] Scraping Pinterest board(s)...")
    records = []
    for board_url in board_urls:
        try:
            board_records = scrape_pinterest_board(board_url, max_pins=10)
            records.extend(board_records)
            print(f"      {board_url.split('/')[-2] or 'board'}: {len(board_records)} pins")
        except Exception as e:
            print(f"      ERROR ({board_url}): {e}")

    if not records:
        print("      No pins found. Check board URLs and Apify actor config.")
        sys.exit(1)

    print(f"      Total: {len(records)} pins")

    # Step 2: Generate embeddings
    print("\n[2/4] Generating CLIP embeddings...")
    try:
        add_embeddings_to_records(records)
        valid = [r for r in records if r.get("embedding")]
        print(f"      Generated {len(valid)}/{len(records)} embeddings")
    except Exception as e:
        print(f"      ERROR: {e}")
        sys.exit(1)

    # Filter out records without embeddings for DB insert
    records = [r for r in records if r.get("embedding")]

    # Step 3: Store in Supabase
    print("\n[3/4] Storing in Supabase...")
    try:
        client = get_supabase_client()
        db_records = []
        for r in records:
            db_records.append({
                "source_platform": r["source_platform"],
                "source_collection": r.get("source_collection"),
                "image_url": r["image_url"],
                "original_title": r.get("original_title"),
                "original_description": r.get("original_description"),
                "original_metadata": r.get("original_metadata", {}),
                "embedding": r["embedding"],
            })
        inserted = insert_images_batch(client, db_records)
        print(f"      Inserted {len(inserted)} images")
    except Exception as e:
        print(f"      ERROR: {e}")
        sys.exit(1)

    # Step 4: Claude analysis
    print("\n[4/4] Running Claude pattern analysis...")
    try:
        from agent.reasoning import analyze_collection

        collection_name = records[0].get("source_collection", "unknown") if records else "unknown"
        sample = [r.get("original_title") or r.get("original_description") or "" for r in records]
        analysis = analyze_collection(
            collection_name=collection_name,
            source_platform="pinterest",
            total_images=len(records),
            sample_descriptions=sample,
        )
        print("      Material vocabulary:", analysis.get("material_vocabulary", []))
        print("      Spatial qualities:", analysis.get("spatial_qualities", []))
        print("      Recurring forms:", analysis.get("recurring_forms", []))
        print("      Color palette:", analysis.get("color_palette", []))
        print("      Mood/atmosphere:", analysis.get("mood_atmosphere", ""))
        print("      Installation directions:", analysis.get("installation_directions", []))

        # Save analysis to collections table
        try:
            upsert_collection(
                client,
                collection_name=collection_name,
                source_platform="pinterest",
                total_images=len(records),
                material_vocabulary=analysis.get("material_vocabulary"),
                spatial_qualities=analysis.get("spatial_qualities"),
                recurring_forms=analysis.get("recurring_forms"),
                color_palette=analysis.get("color_palette"),
                mood_atmosphere=analysis.get("mood_atmosphere"),
                installation_directions=analysis.get("installation_directions"),
            )
            print("      Saved analysis to collections table")
        except Exception as e:
            print(f"      (Could not save to collections: {e})")
    except ValueError as e:
        print(f"      SKIP (no ANTHROPIC_API_KEY): {e}")
    except Exception as e:
        print(f"      ERROR: {e}")

    print("\n" + "=" * 60)
    print("Pipeline complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
