#!/usr/bin/env python3
"""
Name the constellation's themes with Claude.

For each theme in constellation_data.json, shows Claude a handful of its most typical
images and stores a short name ("label") and a one-line note ("analysis") on the theme.
Run after `python -m visualization.constellation` (a rebuild renumbers the themes).

Usage (from tastemaker/ directory):
  python -m visualization.name_themes            # name every theme
  python -m visualization.name_themes --only 3 7 # rename specific themes
"""

import argparse
import base64
import io
import json
import os
import re
import sys
from pathlib import Path

import anthropic
import httpx
from dotenv import load_dotenv
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from agent.reasoning import MODEL, _response_text  # noqa: E402

DATA_PATH = Path(__file__).resolve().parent / "constellation_data.json"
SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
IMAGES_PER_THEME = 6

PROMPT = """These images are the most typical members of one theme in a person's visual research archive (a moodboard collection used for spatial design and installation work).

Name the theme the way an architect would annotate a blueprint: precise, quiet, specific to what is actually in these images.

Reply with only a JSON object:
{"label": "2-4 word name, e.g. Liquid Light or Mycelial Drawing", "note": "a short fragment of 4-8 words naming the materials, forms or qualities they share, e.g. caustics, chrome fluids, refracted water"}"""


def image_source(point: dict) -> str:
    """Same resolution order as the constellation page's getImageUrl()."""
    local = point.get("local_path") or ""
    if re.match(r"^(instagram|pinterest|cosmos|manual)/", local) and SUPABASE_URL:
        return f"{SUPABASE_URL}/storage/v1/object/public/images/{local}"
    if local.startswith("data/"):
        return local
    return point.get("image_url") or ""


def load_jpeg(src: str, http: httpx.Client, size: int = 384) -> bytes:
    raw = (ROOT / src).read_bytes() if src.startswith("data/") else http.get(src).raise_for_status().content
    with Image.open(io.BytesIO(raw)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((size, size))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        return buf.getvalue()


def name_theme(client: anthropic.Anthropic, images: list[bytes]) -> dict:
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(b).decode()}}
        for b in images
    ]
    content.append({"type": "text", "text": PROMPT})
    response = client.messages.create(model=MODEL, max_tokens=4000, messages=[{"role": "user", "content": content}])
    text = _response_text(response)
    match = re.search(r"\{.*\}", text, re.S)
    data = json.loads(match.group(0)) if match else {}
    label, note = str(data.get("label", "")).strip(), str(data.get("note", "")).strip()
    if not label:
        raise ValueError(f"unexpected reply: {text[:120]}")
    return {"label": label, "note": note}


def main():
    parser = argparse.ArgumentParser(description="Name constellation themes with Claude")
    parser.add_argument("--only", nargs="*", type=int, help="Theme ids to (re)name; default: all")
    args = parser.parse_args()

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    points, clusters = data["points"], data["clusters"]
    theme_ids = sorted({p["cluster"] for p in points if p["cluster"] != -1})
    if args.only:
        theme_ids = [t for t in theme_ids if t in set(args.only)]

    client = anthropic.Anthropic()
    named = 0
    with httpx.Client(timeout=30, follow_redirects=True) as http:
        for tid in theme_ids:
            members = sorted((p for p in points if p["cluster"] == tid), key=lambda p: -p.get("membership", 1))
            images = []
            for p in members:
                if len(images) == IMAGES_PER_THEME:
                    break
                src = image_source(p)
                if not src:
                    continue
                try:
                    images.append(load_jpeg(src, http))
                except Exception:
                    continue  # skip an unreachable image, try the next member
            if not images:
                print(f"  theme {tid}: no images could be loaded, skipped")
                continue
            try:
                result = name_theme(client, images)
            except Exception as e:
                print(f"  theme {tid}: naming failed ({e})")
                continue
            meta = clusters.setdefault(str(tid), {})
            meta["label"], meta["analysis"] = result["label"], result["note"]
            named += 1
            print(f"  {tid:>3}  {len(members):>3} img  {result['label']}  —  {result['note']}")
            DATA_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")  # save as we go

    print(f"Named {named} of {len(theme_ids)} themes in {DATA_PATH.name}")


if __name__ == "__main__":
    main()
