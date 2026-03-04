# AGENTS.md

## Cursor Cloud specific instructions

### Project layout

The actual application code lives at `Documents/VibeCoded/Tastemaker/tastemaker/` (not the workspace root). All commands below assume you `cd` there first or use it as the working directory.

### Running the dev server

```bash
cd /workspace/Documents/VibeCoded/Tastemaker/tastemaker
source .venv/bin/activate
python run_app.py          # FastAPI + uvicorn on http://localhost:8000, with --reload
```

### Dependencies

Install into the existing virtualenv:

```bash
cd /workspace/Documents/VibeCoded/Tastemaker/tastemaker
source .venv/bin/activate
pip install -r requirements.txt
```

PyTorch + OpenCLIP are large (~2 GB). First install may take a few minutes.

### Environment variables

Copy `.env.example` to `.env` inside the project directory. The app starts and serves its UI without valid API keys; they are only needed for the ingestion pipeline (Pinterest scraping via Apify, Supabase storage, Claude analysis).

Required keys for full pipeline: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `APIFY_TOKEN`, `ANTHROPIC_API_KEY`.

### Linting and tests

There is no linter or test suite configured in this repository. No `pyproject.toml`, `setup.cfg`, or test directory exists.

### Caveats

- The project has no `docker-compose.yml`; all infrastructure (Supabase/Postgres) is externally hosted.
- Several ingestion modules (`cosmos_scraper`, `instagram_parser`, `figma_parser`, `orchestrator`) are placeholders; only Pinterest scraping is functional.
- `sources.json` in the project root is created at runtime to persist added board/cluster URLs.
- The `python3.12-venv` system package must be installed for venv creation (`sudo apt-get install -y python3.12-venv`).
