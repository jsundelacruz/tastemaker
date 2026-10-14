"""Prompts for Claude agent analysis."""

BOARD_ANALYSIS_PROMPT = """You are an expert visual researcher and aesthetic analyst for spatial design and installation art. Analyze this Pinterest board/collection and extract patterns that inform creative direction.

Board/Collection: {collection_name}
Source: {source_platform}
Number of images: {total_images}

Provide a concise analysis in JSON format with these keys:

- material_vocabulary: list of materials that recur—fabrics, metals, natural elements, surfaces (e.g. "linen", "oxidized copper", "dried botanicals", "raw silk")
- spatial_qualities: list of spatial/structural qualities (e.g. "suspended", "enclosed", "permeable", "floating", "layered")
- recurring_forms: list of recurring forms and structures (e.g. "membranes", "hanging structures", "organic shapes", "soft volumes", "tensile surfaces")
- color_palette: list of dominant colors or palettes (e.g. ["terracotta", "sage", "natural linen"] or "earthy neutrals with ochre accents")
- mood_atmosphere: 1–2 sentences describing the overall mood/atmosphere (e.g. "Serene, contemplative; soft light and organic textures evoke a retreat-like calm.")
- installation_directions: list of 2–4 concrete suggestions for translating this curation into an installation or spatial intervention (e.g. "Explore suspended fabric panels to create permeable boundaries", "Layer textiles at varying heights to suggest depth without enclosure")

Be specific and design-oriented. Focus on what a spatial designer or installation artist could learn from this curation."""

MOODBOARD_IMAGE_ANALYSIS_PROMPT = """This image is a moodboard: a screenshot or collage containing multiple visual clusters (groups of images, references, or distinct aesthetic zones). Your job is to analyze the CONTENT of the moodboard—the actual imagery and themes shown—not the fact that it is a screenshot or digital interface.

Step 1 — Identify clusters: Look at the image and identify the distinct visual clusters or image groups (e.g. "cluster of natural materials", "interior vignettes", "textile swatches", "architectural details"). Describe what you see in each cluster briefly.

Step 2 — Thematic synthesis: From those clusters, extract patterns that inform creative direction. Output a single JSON object with these keys, reflecting the diversity and themes across the clusters:

- material_vocabulary: list of materials that recur across the clusters—fabrics, metals, natural elements, surfaces (e.g. "linen", "oxidized copper", "dried botanicals")
- spatial_qualities: list of spatial/structural qualities suggested by the imagery (e.g. "suspended", "layered", "organic", "geometric")
- recurring_forms: list of recurring forms and structures (e.g. "soft volumes", "grids", "organic shapes", "tensile surfaces")
- color_palette: list of dominant colors or palettes you see in the clusters (e.g. "terracotta", "sage", "earthy neutrals")
- mood_atmosphere: 1–2 sentences describing the overall mood/atmosphere of the moodboard content (the imagery and themes, not the medium)
- installation_directions: list of 2–4 concrete suggestions for translating this curation into an installation or spatial intervention

Be specific to what is actually in the image. Focus on the different images and their thematic aesthetic clusters, not on "screenshot" or "digital" as materials."""

OVERALL_TASTE_PROMPT = """You are an expert visual researcher and taste analyst. Below are summaries of multiple collections (Pinterest boards, Cosmos clusters, moodboards, screenshots) that the reader has saved. Each collection has already been analyzed for materials, spatial qualities, mood, color, and installation directions.

Your task: synthesize everything into a single "overall taste" profile. Write directly to the reader using "you" and "your" (e.g. "Your taste leans toward...", "You gravitate to...").

Output format (follow exactly):

1. First line: theme tags. Write "Themes: " followed by 4–8 comma-separated short phrases that capture the biggest aesthetic themes (e.g. "vaporwave nostalgia, layered transparency, soft gradients, digital decay"). This line will be shown as tags.

2. Then 2–4 short paragraphs in second person describing their overarching aesthetic, recurring themes, materials and moods that tie their taste together, and any tensions or range that make it distinctive.

3. End with exactly: "Creative brief: " followed by one or two sentences that could serve as a creative brief or tagline. Do not use markdown (no asterisks, no bold). Plain text only.

Be specific and design-oriented. Use clear prose (no bullet lists in the paragraphs)."""
