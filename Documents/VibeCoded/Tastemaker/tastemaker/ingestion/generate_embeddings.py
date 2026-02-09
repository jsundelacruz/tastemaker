"""
Generate CLIP embeddings for images.
Uses OpenCLIP (ViT-B/32) for 512-dimensional vectors compatible with pgvector.
"""

import io
from pathlib import Path
from typing import Union

import httpx
import torch
from PIL import Image
import open_clip

# Model produces 512-dim embeddings (ViT-B/32)
EMBEDDING_DIM = 512
MODEL_ID = "ViT-B-32"
PRETRAINED = "openai"

_model = None
_preprocess = None


def _load_model():
    """Lazy-load CLIP model."""
    global _model, _preprocess
    if _model is None:
        _model, _, _preprocess = open_clip.create_model_and_transforms(
            MODEL_ID, pretrained=PRETRAINED
        )
        _model.eval()
    return _model, _preprocess


def encode_image(image: Union[Image.Image, Path, str, bytes]) -> list[float]:
    """
    Generate CLIP embedding for a single image.

    Args:
        image: PIL Image, file path, URL string, or raw bytes

    Returns:
        512-dimensional embedding as list of floats (L2-normalized)
    """
    model, preprocess = _load_model()
    pil_image = _to_pil(image)
    if pil_image is None:
        raise ValueError("Could not load image")

    tensor = preprocess(pil_image).unsqueeze(0)
    with torch.no_grad():
        embedding = model.encode_image(tensor)
        embedding = embedding / embedding.norm(dim=-1, keepdim=True)
    return embedding.squeeze(0).tolist()


def encode_images_batch(images: list[Union[Image.Image, Path, str, bytes]]) -> list[list[float]]:
    """Generate embeddings for multiple images (more efficient)."""
    model, preprocess = _load_model()
    tensors = []
    for img in images:
        pil = _to_pil(img)
        if pil is not None:
            tensors.append(preprocess(pil))
    if not tensors:
        return []
    batch = torch.stack(tensors)
    with torch.no_grad():
        embeddings = model.encode_image(batch)
        embeddings = embeddings / embeddings.norm(dim=-1, keepdim=True)
    return [e.tolist() for e in embeddings]


def _to_pil(source: Union[Image.Image, Path, str, bytes]) -> Image.Image | None:
    """Convert various inputs to PIL Image."""
    if isinstance(source, Image.Image):
        return source.convert("RGB")
    if isinstance(source, (Path, str)):
        path = Path(source)
        if path.exists():
            return Image.open(path).convert("RGB")
        # Assume URL
        if str(source).startswith(("http://", "https://")):
            return _fetch_url(source)
    if isinstance(source, bytes):
        return Image.open(io.BytesIO(source)).convert("RGB")
    return None


def _fetch_url(url: str) -> Image.Image:
    """Fetch image from URL and return PIL Image."""
    resp = httpx.get(url, follow_redirects=True, timeout=30)
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content)).convert("RGB")


def add_embeddings_to_records(
    records: list[dict],
    image_url_key: str = "image_url",
) -> list[dict]:
    """
    Add 'embedding' field to each record. Downloads images from URLs and encodes.

    Modifies records in place and returns them.
    """
    urls = [r.get(image_url_key) for r in records]
    embeddings = []
    for url in urls:
        try:
            emb = encode_image(url)
            embeddings.append(emb)
        except Exception as e:
            # Store None for failed images; caller can filter
            embeddings.append(None)
            print(f"Embedding failed for {url}: {e}")

    for record, emb in zip(records, embeddings):
        record["embedding"] = emb

    return records
