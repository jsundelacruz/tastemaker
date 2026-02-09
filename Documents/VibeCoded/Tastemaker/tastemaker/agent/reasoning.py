"""Claude agent for pattern analysis."""

import json
import os
from typing import Optional

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
