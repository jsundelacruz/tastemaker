"""Claude agent for pattern analysis."""

import base64
import json
import os
from pathlib import Path
from typing import Optional, Union

import anthropic
from dotenv import load_dotenv

load_dotenv()


def analyze_collection(
    collection_name: str,
    source_platform: str,
    total_images: int,
    sample_descriptions: Optional[list[str]] = None,
    api_key: Optional[str] = None,
) -> dict:
    """
    Use Claude to analyze a collection and extract aesthetic patterns.

    Returns dict with material_vocabulary, spatial_qualities, recurring_forms,
    color_palette, mood_atmosphere, installation_directions.
    """
    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY required. Set in .env or pass api_key.")

    from .prompts import BOARD_ANALYSIS_PROMPT

    prompt = BOARD_ANALYSIS_PROMPT.format(
        collection_name=collection_name,
        source_platform=source_platform,
        total_images=total_images,
    )

    if sample_descriptions:
        prompt += "\n\nSample image titles/descriptions from the board:\n"
        for i, d in enumerate(sample_descriptions[:10], 1):
            prompt += f"{i}. {d}\n"

    client = anthropic.Anthropic(api_key=key)
    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text
    # Try to parse JSON from response
    try:
        # Handle markdown code blocks
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "material_vocabulary": [],
            "spatial_qualities": [],
            "recurring_forms": [],
            "color_palette": [],
            "mood_atmosphere": "",
            "installation_directions": [],
            "raw_response": text,
        }


def analyze_moodboard_image(
    image: Union[Path, str, bytes],
    collection_name: str = "moodboard",
    api_key: Optional[str] = None,
) -> dict:
    """
    Use Claude vision to analyze a moodboard image: identify distinct visual
    clusters and their themes, then synthesize the same JSON analysis fields.
    Use this when the source is a screenshot/collage of multiple images.
    """
    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY required. Set in .env or pass api_key.")

    if isinstance(image, (Path, str)):
        path = Path(image)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {path}")
        image_bytes = path.read_bytes()
        media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    else:
        image_bytes = image
        media_type = "image/png"

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    from .prompts import MOODBOARD_IMAGE_ANALYSIS_PROMPT

    client = anthropic.Anthropic(api_key=key)
    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": MOODBOARD_IMAGE_ANALYSIS_PROMPT,
                    },
                ],
            }
        ],
    )

    text = response.content[0].text
    to_try = [text]
    if "```json" in text:
        to_try.append(text.split("```json")[1].split("```")[0].strip())
    elif "```" in text:
        to_try.append(text.split("```")[1].split("```")[0].strip())
    for raw in to_try:
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "material_vocabulary" in parsed:
                return parsed
        except json.JSONDecodeError:
            continue
    return {
        "material_vocabulary": [],
        "spatial_qualities": [],
        "recurring_forms": [],
        "color_palette": [],
        "mood_atmosphere": "",
        "installation_directions": [],
        "raw_response": text,
    }


def analyze_overall_taste(collections: list[dict], api_key: Optional[str] = None) -> str:
    """
    Synthesize all collection analyses into one overall taste profile (prose).
    Uses tokens only when this function is called (e.g. on Re-analyze).
    """
    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY required.")
    from .prompts import OVERALL_TASTE_PROMPT

    blocks = []
    for c in collections:
        name = c.get("collection_name") or "Unnamed"
        platform = c.get("source_platform") or ""
        n = c.get("total_images") or 0
        blocks.append(f"--- {name} ({platform}, {n} images) ---")
        if c.get("material_vocabulary"):
            blocks.append("Materials: " + ", ".join(c["material_vocabulary"][:12]))
        if c.get("spatial_qualities"):
            blocks.append("Spatial: " + ", ".join(c["spatial_qualities"][:8]))
        if c.get("mood_atmosphere"):
            blocks.append("Mood: " + (c["mood_atmosphere"] or "")[:400])
        if c.get("color_palette"):
            blocks.append("Colors: " + ", ".join(c["color_palette"][:8]))
        blocks.append("")

    prompt = OVERALL_TASTE_PROMPT + "\n\n" + "\n".join(blocks)
    client = anthropic.Anthropic(api_key=key)
    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return (response.content[0].text or "").strip()
