# TasteMaker

Visual research archive system that ingests moodboards from multiple sources, generates embeddings for similarity search, and uses an AI agent to analyze aesthetic patterns.

## Tech Stack

- **Backend:** Python + FastAPI
- **Database:** Supabase (Postgres + pgvector)
- **Embeddings:** OpenCLIP (ViT-B/32, 512-dim)
- **Agent:** Claude API
- **Scraping:** Apify

## Setup

### 1. Install dependencies

```bash
cd /Users/jsundelacruz/Documents/VibeCoded/TasteMaker/tastemaker
python3 -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Database setup

1. Create a Supabase project at [supabase.com](https://supabase.com)
2. Run `database/schema.sql` in the Supabase SQL Editor
3. Copy your project URL and service role key to `.env`

### 4. Apify setup

1. Sign up at [apify.com](https://apify.com)
2. Find a Pinterest scraper in the Apify Store (e.g. `red.cars/pinterest-board-scraper`)
3. Set `APIFY_TOKEN` in `.env`
4. Optionally set `APIFY_PINTEREST_ACTOR` to your preferred actor ID

## Selecting Boards

**Only boards you explicitly add are scraped.** Add board URLs to `boards.txt` (one per line):

```
# boards.txt
https://www.pinterest.com/username/moodboard-1/
https://pin.it/7ESoTISS5
```

## Ingestion UI

Web UI for adding sources and running ingestion:

```bash
cd tastemaker
source .venv/bin/activate
python run_app.py
```

Open http://localhost:8000 — add Pinterest boards, Cosmos clusters, and paste/drop screenshots.

## Quick Test

Run the end-to-end pipeline from the command line. From the TasteMaker workspace (parent of `tastemaker`):

```bash
cd tastemaker
source .venv/bin/activate
# Process all boards in boards.txt
python3 scripts/quick_test.py

# Or process a single board
python3 scripts/quick_test.py "https://pin.it/7ESoTISS5"
```

This will:
1. Scrape the Pinterest board via Apify
2. Generate CLIP embeddings for each image
3. Store images + embeddings in Supabase
4. Run Claude analysis for pattern detection

## Project Structure

```
tastemaker/
├── ingestion/
│   ├── pinterest_scraper.py   # Apify Pinterest integration
│   ├── cosmos_scraper.py     # (placeholder)
│   ├── instagram_parser.py   # (placeholder)
│   ├── figma_parser.py       # (placeholder)
│   ├── generate_embeddings.py
│   └── orchestrator.py       # (placeholder)
├── database/
│   ├── schema.sql
│   └── db_client.py
├── agent/
│   ├── prompts.py
│   ├── reasoning.py
│   └── vector_search.py
├── scripts/
│   └── quick_test.py
└── requirements.txt
```

## API Keys Required

| Service | Env Var | Purpose |
|---------|---------|---------|
| Apify | `APIFY_TOKEN` | Pinterest scraping |
| Supabase | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | Storage + pgvector |
| Anthropic | `ANTHROPIC_API_KEY` | Claude pattern analysis |
