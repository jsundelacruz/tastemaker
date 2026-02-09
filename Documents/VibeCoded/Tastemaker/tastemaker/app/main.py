"""
TasteMaker ingestion UI - FastAPI app.
Add Pinterest boards, Cosmos clusters, and paste screenshots.
"""

import json
import base64
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse

# Project root
ROOT = Path(__file__).resolve().parent.parent
SOURCES_FILE = ROOT / "sources.json"
UPLOADS_DIR = ROOT / "data" / "uploads"


def _load_sources() -> dict:
    if not SOURCES_FILE.exists():
        return {"pinterest": [], "cosmos": [], "screenshots": []}
    return json.loads(SOURCES_FILE.read_text())


def _save_sources(data: dict) -> None:
    SOURCES_FILE.write_text(json.dumps(data, indent=2))


def _ensure_uploads() -> Path:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOADS_DIR


app = FastAPI(title="TasteMaker Ingestion")


@app.get("/", response_class=HTMLResponse)
async def index():
    path = ROOT / "app" / "static" / "index.html"
    if not path.exists():
        raise HTTPException(404, "Static file not found")
    return FileResponse(path)


@app.get("/api/sources")
async def get_sources():
    return _load_sources()


@app.post("/api/pinterest")
async def add_pinterest(url: str = Form(...)):
    """Add a Pinterest board URL."""
    url = url.strip()
    if not url or ("pinterest.com" not in url and "pin.it" not in url):
        raise HTTPException(400, "Invalid Pinterest URL")
    sources = _load_sources()
    if url not in sources["pinterest"]:
        sources["pinterest"].append(url)
        _save_sources(sources)
    return {"added": url, "sources": sources}


@app.post("/api/cosmos")
async def add_cosmos(url: str = Form(...)):
    """Add a Cosmos cluster URL."""
    url = url.strip()
    if not url or "cosmos.so" not in url:
        raise HTTPException(400, "Invalid Cosmos URL")
    sources = _load_sources()
    if url not in sources["cosmos"]:
        sources["cosmos"].append(url)
        _save_sources(sources)
    return {"added": url, "sources": sources}


@app.delete("/api/pinterest/{idx}")
async def remove_pinterest(idx: int):
    sources = _load_sources()
    if 0 <= idx < len(sources["pinterest"]):
        sources["pinterest"].pop(idx)
        _save_sources(sources)
    return {"sources": sources}


@app.delete("/api/cosmos/{idx}")
async def remove_cosmos(idx: int):
    sources = _load_sources()
    if 0 <= idx < len(sources["cosmos"]):
        sources["cosmos"].pop(idx)
        _save_sources(sources)
    return {"sources": sources}


@app.delete("/api/screenshot/{idx}")
async def remove_screenshot(idx: int):
    sources = _load_sources()
    if 0 <= idx < len(sources["screenshots"]):
        sources["screenshots"].pop(idx)
        _save_sources(sources)
    return {"sources": sources}


@app.post("/api/screenshot")
async def add_screenshot(
    file: UploadFile = File(None),
    data: str = Form(None),
):
    """
    Add screenshot via file upload or base64 paste.
    """
    _ensure_uploads()
    if file:
        ext = Path(file.filename or "png").suffix or ".png"
        path = UPLOADS_DIR / f"{uuid.uuid4().hex}{ext}"
        path.write_bytes(await file.read())
    elif data:
        # base64 data URL from paste
        if "base64," in data:
            b64 = data.split("base64,")[1]
            img_bytes = base64.b64decode(b64)
        else:
            img_bytes = base64.b64decode(data)
        path = UPLOADS_DIR / f"{uuid.uuid4().hex}.png"
        path.write_bytes(img_bytes)
    else:
        raise HTTPException(400, "Provide file or base64 data")

    rel_path = str(path.relative_to(ROOT))
    sources = _load_sources()
    sources["screenshots"].append(rel_path)
    _save_sources(sources)
    return {"added": rel_path, "sources": sources}


@app.post("/api/ingest")
async def run_ingest():
    """Run the ingestion pipeline on all configured sources."""
    try:
        return await _run_ingest()
    except Exception as e:
        return {"error": str(e), "records": 0}


async def _run_ingest():
    import sys
    sys.path.insert(0, str(ROOT))

    from ingestion.pinterest_scraper import scrape_pinterest_board
    from ingestion.generate_embeddings import add_embeddings_to_records
    from database.db_client import get_supabase_client, insert_images_batch, upsert_collection
    from agent.reasoning import analyze_collection

    sources = _load_sources()
    records = []

    # Pinterest
    for url in sources["pinterest"]:
        try:
            board_records = scrape_pinterest_board(url, max_pins=10)
            records.extend(board_records)
        except Exception as e:
            return {"error": f"Pinterest {url}: {e}", "records": len(records)}

    # Cosmos (placeholder - scraper not yet implemented)
    for url in sources["cosmos"]:
        pass  # TODO: cosmos_scraper.scrape_cluster(url)

    # Screenshots
    for rel_path in sources["screenshots"]:
        path = ROOT / rel_path
        if path.exists():
            records.append({
                "source_platform": "manual",
                "source_collection": "screenshots",
                "image_url": "",  # no URL for local
                "local_path": rel_path,
                "original_title": path.stem,
                "original_description": None,
                "original_metadata": {},
            })

    if not records:
        return {"message": "No sources to ingest", "records": 0}

    # Generate embeddings - use local path for screenshots, URL for Pinterest
    from ingestion.generate_embeddings import add_embeddings_to_records

    for r in records:
        if r.get("local_path") and not r.get("image_url"):
            r["image_url"] = str(ROOT / r["local_path"])  # encode_image accepts Path

    add_embeddings_to_records(records)
    records = [r for r in records if r.get("embedding")]

    # Store in Supabase
    client = get_supabase_client()
    db_records = []
    for r in records:
        img_url = r.get("image_url") or ""
        if r.get("local_path") and not img_url.startswith("http"):
            img_url = f"file://{ROOT / r['local_path']}"  # placeholder for local
        db_records.append({
            "source_platform": r["source_platform"],
            "source_collection": r.get("source_collection"),
            "image_url": img_url or "file://local",
            "local_path": r.get("local_path"),
            "original_title": r.get("original_title"),
            "original_description": r.get("original_description"),
            "original_metadata": r.get("original_metadata", {}),
            "embedding": r.get("embedding"),
        })
    inserted = insert_images_batch(client, db_records)

    # Claude analysis
    collection_name = records[0].get("source_collection", "archive") if records else "archive"
    source_platform = records[0].get("source_platform", "pinterest") if records else "pinterest"
    sample = [r.get("original_title") or r.get("original_description") or "" for r in records[:10]]
    try:
        analysis = analyze_collection(
            collection_name=collection_name,
            source_platform="pinterest",
            total_images=len(records),
            sample_descriptions=sample,
        )
        upsert_collection(
            client,
            collection_name=collection_name,
            source_platform=source_platform,
            total_images=len(records),
            **{k: v for k, v in analysis.items() if k in ["material_vocabulary", "spatial_qualities", "recurring_forms", "color_palette", "mood_atmosphere", "installation_directions"]},
        )
    except Exception as e:
        analysis = {"error": str(e)}

    return {
        "records": len(inserted),
        "analysis": analysis,
    }


# Mount static files
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
