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
