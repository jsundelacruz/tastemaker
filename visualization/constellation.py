#!/usr/bin/env python3
"""
Build 2D constellation data from the images table.

HOW CLUSTERING WORKS (the "rule"):
  1. UMAP: Each image has a 512-dim CLIP embedding. UMAP reduces 512D → 2D so that
     similar images end up close and dissimilar farther apart. Parameters:
     n_components=2, min_dist=0.3 (more spread), n_neighbors=15.
  2. Coordinates are scaled (95th percentile norm, then 2x for breathing room).
  3. HDBSCAN finds dense regions; each gets a cluster label. Noise = -1.
  4. Cluster separation: each cluster's points are pushed outward from the global
     centroid in the direction of their cluster centroid to create visible gaps.
  5. Convex hulls (with padding) and cluster metadata (label, color, analysis) are
     computed for the frontend.

Output: visualization/constellation_data.json with points (x, y only), cluster ids,
and a "clusters" map with label, color, analysis, and hull polygon per cluster.

Usage (from tastemaker/ directory):
  python -m visualization.constellation
  python visualization/constellation.py
"""

import json
import os
import sys
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from supabase import create_client

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
OUTPUT_PATH = Path(__file__).resolve().parent / "constellation_data.json"


def fetch_images(client):
    """Fetch id, source_collection, original_description, image_url, local_path, embedding, original_metadata from images."""
    result = (
        client.table("images")
        .select("id, source_collection, original_description, image_url, local_path, embedding, original_metadata")
        .not_.is_("embedding", "null")
        .execute()
    )
    return result.data or []


def embeddings_to_array(rows):
    """Extract embeddings as numpy array (n, 512); return rows with valid embeddings."""
    arr = []
    kept = []
    for r in rows:
        emb = r.get("embedding")
        if emb is None:
            continue
        if isinstance(emb, str):
            try:
                emb = json.loads(emb)
            except json.JSONDecodeError:
                continue
        if not isinstance(emb, (list, tuple)) or len(emb) != 512:
            continue
        arr.append(emb)
        kept.append(r)
    return np.array(arr, dtype=np.float32), kept


def cluster_color(cluster_id: int) -> str:
    """Return hex color for cluster (deterministic from id). Noise = grey."""
    if cluster_id == -1:
        return "#6b6b6b"
    hue = (cluster_id * 0.618033989) % 1.0
    # HSL to RGB, S=0.5 L=0.65
    from colorsys import hls_to_rgb
    r, g, b = hls_to_rgb(hue, 0.65, 0.5)
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


def convex_hull_with_padding(points: np.ndarray, padding: float) -> list[list[float]]:
    """Return convex hull as list of [x,y] with outward padding. points shape (n, 2)."""
    try:
        from scipy.spatial import ConvexHull
    except ImportError:
        # Fallback: bounding box with padding
        if len(points) == 0:
            return []
        min_xy = points.min(axis=0)
        max_xy = points.max(axis=0)
        cx = (min_xy[0] + max_xy[0]) / 2
        cy = (min_xy[1] + max_xy[1]) / 2
        w = max(max_xy[0] - min_xy[0], 0.1) / 2 + padding
        h = max(max_xy[1] - min_xy[1], 0.1) / 2 + padding
        return [
            [cx - w, cy - h], [cx + w, cy - h], [cx + w, cy + h], [cx - w, cy + h],
        ]
    if len(points) < 3:
        if len(points) == 2:
            d = points[1] - points[0]
            n = np.array([-d[1], d[0]])
            n = n / (np.linalg.norm(n) + 1e-9) * padding
            return [
                (points[0] - n).tolist(),
                (points[0] + n).tolist(),
                (points[1] + n).tolist(),
                (points[1] - n).tolist(),
            ]
        return [[float(p[0]), float(p[1])] for p in points]
    hull = ConvexHull(points)
    vertices = points[hull.vertices]  # (n_verts, 2)
    # Center of hull
    centroid = vertices.mean(axis=0)
    # Push each vertex outward by padding
    out = []
    for v in vertices:
        d = v - centroid
        n = np.linalg.norm(d)
        if n > 1e-9:
            d = d / n
        out.append((v + padding * d).tolist())
    return [[float(x), float(y)] for x, y in out]


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: Set SUPABASE_URL and SUPABASE_KEY (or SUPABASE_SERVICE_KEY) in .env", file=sys.stderr)
        sys.exit(1)

    print("Connecting to Supabase...")
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    rows = fetch_images(client)
    if not rows:
        print("No image rows with embeddings found.")
        sys.exit(0)

    print(f"Fetched {len(rows)} rows. Converting embeddings...")
    X, kept = embeddings_to_array(rows)
    if len(kept) == 0:
        print("No valid embeddings.")
        sys.exit(0)
    n = X.shape[0]
    print(f"Reducing {n} points from 512D to 2D (UMAP, min_dist=0.3)...")

    import umap
    reducer = umap.UMAP(n_neighbors=15, min_dist=0.3, n_components=2, random_state=42)
    coords_2d = reducer.fit_transform(X)

    # Scale by 95th percentile of radius, then 3x for more breathing room
    radii = np.linalg.norm(coords_2d, axis=1)
    scale = float(np.percentile(radii, 95)) or 1.0
    coords_2d = coords_2d / scale
    coords_2d = coords_2d * 3.0

    # Themes: cluster in a 10D UMAP space (keeps far more structure than the 2D layout).
    # "leaf" selection finds the finest stable themes; the default merges this archive into ~2 blobs.
    print("Finding themes (UMAP 10D + HDBSCAN leaf)...")
    import hdbscan
    themes_space = umap.UMAP(n_neighbors=30, min_dist=0.0, n_components=10, metric="cosine", random_state=42).fit_transform(X)
    clusterer = hdbscan.HDBSCAN(min_cluster_size=8, min_samples=3, cluster_selection_method="leaf")
    labels = clusterer.fit_predict(themes_space)
    membership = clusterer.probabilities_.astype(float)  # 0..1: how strongly each image belongs to its theme
    unique_labels = sorted(set(labels) - {-1})
    loose = labels == -1
    # Attach loosely-related images to their nearest theme (membership 0 → placed behind the theme's core)
    if unique_labels and loose.any():
        centroids = np.stack([themes_space[labels == cid].mean(axis=0) for cid in unique_labels])
        nearest = np.linalg.norm(themes_space[loose][:, None, :] - centroids[None, :, :], axis=2).argmin(axis=1)
        labels = labels.copy()
        labels[loose] = np.array(unique_labels)[nearest]
        membership[loose] = 0.0
    sizes = {cid: int(np.sum(labels == cid)) for cid in unique_labels}
    print(f"  Found {len(unique_labels)} themes; {int(loose.sum())} loosely-related images attached to their nearest theme.")

    # Global centroid
    global_centroid = np.mean(coords_2d, axis=0)

    # Push each cluster outward from global centroid (cluster separation)
    push_strength = 1.0
    for cid in unique_labels:
        mask = labels == cid
        cluster_centroid = coords_2d[mask].mean(axis=0)
        direction = cluster_centroid - global_centroid
        norm = np.linalg.norm(direction)
        if norm > 1e-9:
            direction = direction / norm
        offset = direction * push_strength
        coords_2d[mask] += offset

    # Build per-cluster metadata and hulls
    clusters = {}
    hull_padding = 0.15
    for cid in unique_labels:
        mask = labels == cid
        pts = coords_2d[mask]
        hull = convex_hull_with_padding(pts, hull_padding)
        label = f"Cluster {cid}" if cid >= 0 else "Unclustered"
        clusters[str(cid)] = {
            "label": label,
            "color": cluster_color(cid),
            "analysis": "",  # Placeholder for Claude-generated analysis
            "hull": hull,
        }
    clusters["-1"] = {
        "label": "Unclustered",
        "color": cluster_color(-1),
        "analysis": "Images that didn’t belong to a dense visual cluster.",
        "hull": [],
    }

    out_points = []
    for i, row in enumerate(kept):
        r = row.copy()
        rid = r.get("id")
        if isinstance(rid, dict) and rid.get("__typename") == "uuid":
            rid = str(rid.get("value", rid))
        meta = r.get("original_metadata") or {}
        post_url = (meta.get("post_url") or meta.get("pin_url") or "").strip() or None
        out_points.append({
            "id": str(rid) if rid is not None else str(i),
            "x": float(coords_2d[i, 0]),
            "y": float(coords_2d[i, 1]),
            "cluster": int(labels[i]),
            "cluster_size": sizes.get(int(labels[i]), 1),
            "membership": round(float(membership[i]), 3),
            "username": (r.get("source_collection") or "").strip() or None,
            "description": (r.get("original_description") or "").strip() or None,
            "image_url": (r.get("image_url") or "").strip() or None,
            "local_path": (r.get("local_path") or "").strip() or None,
            "post_url": post_url,
        })

    payload = {
        "points": out_points,
        "clusters": clusters,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote {len(out_points)} points and {len(clusters)} cluster entries to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
