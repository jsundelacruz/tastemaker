"""
TasteMaker ingestion UI - FastAPI app.
Add Pinterest boards, Cosmos clusters, and paste screenshots.
"""

import json
import base64
import uuid
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Body, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, Response, PlainTextResponse
from dotenv import load_dotenv

# Project root (tastemaker directory)
ROOT = Path(__file__).resolve().parent.parent
# Load .env from project root so it works regardless of cwd
load_dotenv(ROOT / ".env")
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

# All pages are served by this app, so no CORS: other sites can't call the API from a browser.

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
AUTH_EXEMPT = {"/healthz"}


@app.middleware("http")
async def require_password(request: Request, call_next):
    """
    HTTP Basic auth (any username) against TASTEMAKER_PASSWORD.
    With no password set, only requests from this machine are allowed, so a deploy
    without one fails closed instead of exposing the archive and paid endpoints.
    """
    if request.url.path in AUTH_EXEMPT:
        return await call_next(request)
    password = os.getenv("TASTEMAKER_PASSWORD", "")
    if not password:
        if request.client and request.client.host in LOCAL_HOSTS:
            return await call_next(request)
        return PlainTextResponse("Set TASTEMAKER_PASSWORD to allow remote access.", status_code=403)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            _, _, given = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
        except Exception:
            given = ""
        if secrets.compare_digest(given.encode(), password.encode()):
            return await call_next(request)
    return PlainTextResponse(
        "Authentication required", status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="TasteMaker"'},
    )


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def index():
    path = ROOT / "app" / "static" / "index.html"
    if not path.exists():
        raise HTTPException(404, "Static file not found")
    return FileResponse(path)


@app.get("/analyses", response_class=HTMLResponse)
async def analyses_page():
    path = ROOT / "app" / "static" / "analyses.html"
    if not path.exists():
        raise HTTPException(404, "Static file not found")
    return FileResponse(path)


@app.get("/your-taste", response_class=HTMLResponse)
async def your_taste_page():
    path = ROOT / "app" / "static" / "your-taste.html"
    if not path.exists():
        raise HTTPException(404, "Static file not found")
    return FileResponse(path)


@app.get("/my-taste", response_class=HTMLResponse)
async def my_taste_redirect():
    return RedirectResponse(url="/your-taste", status_code=302)


@app.get("/find-similar", response_class=HTMLResponse)
async def find_similar_page():
    path = ROOT / "app" / "static" / "find-similar.html"
    if not path.exists():
        raise HTTPException(404, "Static file not found")
    return FileResponse(path)


@app.get("/constellation", response_class=HTMLResponse)
async def constellation_page():
    """3D constellation visualization of image archive."""
    path = ROOT / "visualization" / "index.html"
    if not path.exists():
        raise HTTPException(404, "Constellation not found")
    try:
        html = path.read_text(encoding="utf-8")
        # Inject Supabase URL for storage access
        url = os.getenv("SUPABASE_URL", "")
        # Replace SUPABASE_URL placeholder if it exists, or add it
        if "const SUPABASE_URL" in html:
            html = re.sub(r"const SUPABASE_URL\s*=\s*[^;]+;", f"const SUPABASE_URL = {repr(url)};", html, count=1)
        else:
            # Add it after the script tag starts
            html = html.replace("<script type=\"module\">", f"<script type=\"module\">\n    const SUPABASE_URL = {repr(url)};")
        return HTMLResponse(html)
    except Exception as e:
        raise HTTPException(500, f"Cannot read constellation.html: {e}")


@app.get("/constellation_data.json")
async def constellation_data():
    """Serve constellation JSON for the 3D viewer (same-origin fetch)."""
    path = ROOT / "visualization" / "constellation_data.json"
    if not path.exists():
        raise HTTPException(404, "Run: python -m visualization.constellation to generate data")
    return FileResponse(path, media_type="application/json")


@app.get("/curate", response_class=HTMLResponse)
async def curate_page():
    """Curation tool: grid of images, mark and delete by id (data via /api/images)."""
    path = ROOT / "visualization" / "curate.html"
    if not path.exists():
        raise HTTPException(404, f"Curate not found at {path}")
    return FileResponse(path)


@app.get("/api/images")
async def get_images():
    """All images (id, url, collection, local path) for the curation grid."""
    try:
        from database.db_client import get_supabase_client, list_images
        return list_images(get_supabase_client())
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/images/delete")
async def delete_images(body: dict = Body(...)):
    """Delete images by id. Body: {"ids": [...]}."""
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise HTTPException(400, "ids must be a non-empty list of strings")
    try:
        from database.db_client import get_supabase_client, delete_images_by_id
        delete_images_by_id(get_supabase_client(), ids)
        return {"deleted": len(ids)}
    except Exception as e:
        raise HTTPException(500, str(e))


def _taste_needs_reanalysis(client) -> bool:
    """True if there are new/updated collections since last taste run (no tokens, DB only)."""
    from database.db_client import get_user_taste, get_latest_collection_updated_at
    taste = get_user_taste(client)
    latest = get_latest_collection_updated_at(client)
    if not latest:
        return False
    if not taste or not taste.get("updated_at"):
        return True
    return latest > taste["updated_at"]


@app.get("/api/taste")
async def get_taste():
    """Return stored overall taste and whether it needs reanalysis."""
    try:
        from database.db_client import get_supabase_client, get_user_taste, get_latest_collection_updated_at
        client = get_supabase_client()
        taste = get_user_taste(client)
        needs = _taste_needs_reanalysis(client)
        return {
            "content": taste.get("content") if taste else None,
            "updated_at": taste.get("updated_at") if taste else None,
            "needs_reanalysis": needs,
        }
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@app.get("/api/taste/status")
async def get_taste_status():
    """Lightweight: only needs_reanalysis (for notification dot on tab). No tokens."""
    try:
        from database.db_client import get_supabase_client
        client = get_supabase_client()
        return {"needs_reanalysis": _taste_needs_reanalysis(client)}
    except Exception:
        return {"needs_reanalysis": False}


@app.post("/api/taste/analyze")
async def run_taste_analyze():
    """Run Claude over all collections, save overall taste, return new content."""
    try:
        from database.db_client import get_supabase_client, list_collections, set_user_taste
        from agent.reasoning import analyze_overall_taste
        client = get_supabase_client()
        collections = list_collections(client)
        if not collections:
            raise HTTPException(400, "No collections to analyze. Ingest some sources first.")
        content = analyze_overall_taste(collections)
        row = set_user_taste(client, content)
        return {"content": content, "updated_at": row.get("updated_at")}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/collections")
async def get_collections():
    """List all collections with analysis, sample images, and source link."""
    try:
        from database.db_client import get_supabase_client, list_collections, get_sample_images
        client = get_supabase_client()
        collections = list_collections(client)
        for c in collections:
            name = c.get("collection_name") or ""
            platform = c.get("source_platform") or ""
            samples = get_sample_images(client, name, platform, limit=6)
            source_url = None
            sample_urls = []
            for s in samples:
                meta = s.get("original_metadata") or {}
                if not source_url:
                    source_url = meta.get("board_url") or meta.get("cluster_url")
                img_url = (s.get("image_url") or "").strip()
                if img_url.startswith("http"):
                    sample_urls.append(img_url)
                elif s.get("local_path"):
                    rel = s["local_path"].replace("\\", "/").lstrip("/")
                    if rel.startswith("data/"):
                        sample_urls.append(f"/api/local-image?path={rel}")
            c["sample_image_urls"] = sample_urls[:6]
            c["source_url"] = source_url
        return collections
    except Exception as e:
        raise HTTPException(500, str(e))


@app.delete("/api/collections/{collection_id}")
async def delete_collection(collection_id: str):
    """Delete a collection and all its images."""
    try:
        from database.db_client import get_supabase_client, delete_collection_and_images
        client = get_supabase_client()
        delete_collection_and_images(client, collection_id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.patch("/api/collections/{collection_id}")
async def patch_collection(collection_id: str, body: dict = Body(...)):
    """Update collection (e.g. collection_name for display title)."""
    try:
        from database.db_client import get_supabase_client, update_collection_name
        client = get_supabase_client()
        new_name = body.get("collection_name") if isinstance(body.get("collection_name"), str) else None
        if new_name is None:
            raise HTTPException(400, "collection_name required")
        updated = update_collection_name(client, collection_id, new_name)
        return updated
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/find-similar")
async def find_similar(file: UploadFile = File(...), limit: int = 12):
    """Upload an image; return similar images from the archive (by embedding)."""
    try:
        from fastapi.concurrency import run_in_threadpool
        from database.db_client import get_supabase_client, search_similar_images, get_images_metadata
        from ingestion.generate_embeddings import encode_image
        content = await file.read()
        if not content:
            raise HTTPException(400, "Empty file")
        # CLIP inference is CPU-bound; keep it off the event loop
        embedding = await run_in_threadpool(encode_image, content)
        client = get_supabase_client()
        matches = search_similar_images(client, embedding, limit=max(1, min(limit, 24)))
        metadata = get_images_metadata(client, [str(m.get("id")) for m in matches])
        out = []
        for m in matches:
            img_url = (m.get("image_url") or "").strip()
            display_url = img_url
            if img_url.startswith("file://"):
                try:
                    p = Path(img_url.replace("file://", ""))
                    if str(ROOT) in str(p) and p.exists():
                        rel = str(p.relative_to(ROOT)).replace("\\", "/")
                        if rel.startswith("data/"):
                            display_url = f"/api/local-image?path={rel}"
                except Exception:
                    pass
            meta = metadata.get(str(m.get("id")), {})
            out.append({
                "id": str(m.get("id")),
                "image_url": img_url,
                "display_url": display_url,
                "source_url": meta.get("pin_url") or meta.get("post_url") or meta.get("board_url") or meta.get("cluster_url"),
                "source_platform": m.get("source_platform"),
                "source_collection": m.get("source_collection"),
                "similarity": round(float(m.get("similarity", 0)) * 100, 1),
            })
        return {"matches": out}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/local-image")
async def serve_local_image(path: str):
    """Serve an uploaded image from data/uploads (for analyses thumbnails)."""
    if not path or ".." in path or path.startswith("/"):
        raise HTTPException(400, "Invalid path")
    if not path.startswith("data/"):
        raise HTTPException(403, "Only data/ paths allowed")
    full = ROOT / path
    if not full.exists() or not full.is_file():
        raise HTTPException(404, "Not found")
    try:
        return FileResponse(full, media_type="image/png" if path.lower().endswith(".png") else "image/jpeg")
    except Exception:
        raise HTTPException(404, "Not found")


# Image hosts the archive actually uses; the proxy refuses anything else (no open proxy / SSRF)
PROXY_HOST_SUFFIXES = ("cosmos.so", "pinimg.com", "cdninstagram.com", "fbcdn.net", "supabase.co")
PROXY_MAX_BYTES = 15 * 1024 * 1024


def _proxy_host_allowed(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == h or host.endswith("." + h) for h in PROXY_HOST_SUFFIXES)


@app.get("/api/proxy-image")
async def proxy_image(url: str):
    """Proxy images from known CDNs that block hotlinking."""
    import httpx
    if not _proxy_host_allowed(url):
        raise HTTPException(400, "URL not allowed")
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            for _ in range(3):  # follow redirects manually so each hop is re-checked
                resp = await client.get(url)
                if not resp.is_redirect:
                    break
                url = str(resp.next_request.url) if resp.next_request else ""
                if not _proxy_host_allowed(url):
                    raise HTTPException(400, "Redirect not allowed")
            resp.raise_for_status()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch image: {e}")
    content_type = resp.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        raise HTTPException(502, "Not an image")
    if len(resp.content) > PROXY_MAX_BYTES:
        raise HTTPException(502, "Image too large")
    return Response(content=resp.content, media_type=content_type)


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
    """Run the ingestion pipeline on all configured sources. Clears sources on success."""
    try:
        result = await _run_ingest()
        if "error" not in result and "results" in result and result.get("results"):
            _save_sources({"pinterest": [], "cosmos": [], "screenshots": []})
        return result
    except Exception as e:
        return {"error": str(e), "results": [], "total_records": 0}


async def _run_ingest():
    import sys
    sys.path.insert(0, str(ROOT))

    from ingestion.pinterest_scraper import scrape_pinterest_board
    from ingestion.cosmos_scraper import scrape_cosmos_cluster
    from ingestion.generate_embeddings import add_embeddings_to_records
    from database.db_client import get_supabase_client, insert_images_batch, upsert_collection, delete_images_for_collection
    from agent.reasoning import analyze_collection, analyze_moodboard_image

    sources = _load_sources()
    groups = []  # (source_type, source_label, source_url, records)

    # Pinterest: one group per board
    for url in sources["pinterest"]:
        try:
            board_records = scrape_pinterest_board(url, max_pins=10)
            if board_records:
                name = board_records[0].get("source_collection", "pinterest")
                groups.append(("pinterest", name, url, board_records))
        except Exception as e:
            return {"error": f"Pinterest {url}: {e}", "results": [], "total_records": 0}

    # Cosmos: one group per cluster
    for url in sources["cosmos"]:
        try:
            cosmos_records = scrape_cosmos_cluster(url, max_elements=20)
            if cosmos_records:
                name = cosmos_records[0].get("source_collection", "cosmos")
                groups.append(("cosmos", name, url, cosmos_records))
        except Exception as e:
            return {"error": f"Cosmos {url}: {e}", "results": [], "total_records": 0}

    # Screenshots: one group per screenshot (each gets its own collection)
    for rel_path in sources["screenshots"]:
        path = ROOT / rel_path
        if path.exists():
            name = path.stem or "screenshot"
            groups.append(("manual", name, rel_path, [{
                "source_platform": "manual",
                "source_collection": name,
                "image_url": "",
                "local_path": rel_path,
                "original_title": path.stem,
                "original_description": None,
                "original_metadata": {},
            }]))

    if not groups:
        return {"message": "No sources to ingest", "results": [], "total_records": 0}

    from ingestion.generate_embeddings import add_embeddings_to_records

    client = get_supabase_client()
    analysis_keys = ["material_vocabulary", "spatial_qualities", "recurring_forms", "color_palette", "mood_atmosphere", "installation_directions"]
    results = []
    total_records = 0

    for source_type, source_label, source_url, records in groups:
        for r in records:
            if r.get("local_path") and not r.get("image_url"):
                r["image_url"] = str(ROOT / r["local_path"])
        add_embeddings_to_records(records)
        records = [r for r in records if r.get("embedding")]
        if not records:
            results.append({
                "source_type": source_type,
                "source_label": source_label,
                "source_url": source_url,
                "collection_name": source_label,
                "records_count": 0,
                "analysis": {"error": "No embeddings generated"},
            })
            continue

        collection_name = records[0].get("source_collection", source_label)
        source_platform = records[0].get("source_platform", "pinterest")
        delete_images_for_collection(client, collection_name, source_platform)
        db_records = []
        for r in records:
            img_url = r.get("image_url") or ""
            if r.get("local_path") and not img_url.startswith("http"):
                img_url = f"file://{ROOT / r['local_path']}"
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
        total_records += len(inserted)

        try:
            screenshot_path = None
            if source_platform == "manual":
                for r in records:
                    if r.get("local_path"):
                        p = ROOT / r["local_path"]
                        if p.exists():
                            screenshot_path = p
                            break
            if screenshot_path and source_platform == "manual":
                analysis = analyze_moodboard_image(screenshot_path, collection_name=collection_name)
            else:
                sample = [r.get("original_title") or r.get("original_description") or "" for r in records[:10]]
                analysis = analyze_collection(
                    collection_name=collection_name,
                    source_platform=source_platform,
                    total_images=len(records),
                    sample_descriptions=sample,
                )
            upsert_collection(
                client,
                collection_name=collection_name,
                source_platform=source_platform,
                total_images=len(records),
                **{k: v for k, v in analysis.items() if k in analysis_keys},
            )
        except Exception as e:
            analysis = {"error": str(e)}

        results.append({
            "source_type": source_type,
            "source_label": source_label,
            "source_url": source_url,
            "collection_name": collection_name,
            "records_count": len(inserted),
            "analysis": analysis,
        })

    return {"results": results, "total_records": total_records}


# Mount static files
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
