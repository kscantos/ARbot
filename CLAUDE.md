# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

`arbot` is a FastAPI service that acts as a Lark (Feishu) chat bot for searching alert
documentation, using `sentence-transformers` embeddings (cosine similarity via `numpy`, no
vector DB) over alert sections pulled from local Markdown files and Lark Wiki/Docx documents.

Endpoints (all defined in `app.py`):

- `GET /` — health check, also reports `sources_loaded` and `len(alerts)` so you can tell at a
  glance whether the index actually populated.
- `POST /chat` — plain JSON API (`ChatRequest` → `ChatResponse`) for querying alerts directly.
- `POST /lark/webhook` — Lark event webhook. Handles the URL verification handshake (`challenge`
  echo), then routes incoming text messages: `/s <query>` runs an alert search and replies with an
  interactive card; `ping` round-trips the Lark auth API and replies with latency; anything else
  goes to `chat_reply` (a small canned/greeting responder, not an LLM).

## Architecture: everything lives in `app.py`

Despite `models.py`, `config.py`, `utils.py`, `lark_client.py`, `lark_cards.py`, and `alert.py`
existing as files in the repo root, **`app.py` does not import any of them** — it is a
self-contained monolith that inlines config, the Lark API client, the alert-search/embedding
logic, and the Lark card builders directly. Those six module files are leftover/duplicate copies
of an earlier split-file version of this same logic and are currently dead code from `app.py`'s
perspective. Before editing one of them, check whether `app.py` actually has its own copy of the
same function — it almost certainly does, and that copy is the one that runs.

Within `app.py`, the pieces to know:

- **Startup (`lifespan`)** builds the entire in-memory alert index once, synchronously, before the
  app starts serving:
  1. Reads every `*.md` file in `alertdocs/` and splits it into sections on `#`/`##` headings
     (`md_to_sections`).
  2. Fetches a hardcoded dict of Lark Wiki/Docx sources (`lark_docs` in `lifespan`, currently
     pointing at specific `Test_*` node/doc tokens), resolving wiki node tokens to `obj_token`s via
     `fetch_wiki_obj_token`, then pages through `fetch_doc_blocks` and splits on heading blocks
     (`blocks_to_sections`).
  3. Every section, regardless of source, goes through `register_sections` → `parse_fields`, which
     looks for `Severity:` / `Tag:` / `Action:` labeled lines and falls back to heuristics (e.g. a
     leading all-caps word like `CRITICAL`/`WARNING` as severity) when a doc doesn't label them
     explicitly, and truncates the action body at a literal `"Alert Example"` line if present.
  4. Embeddings are computed once over `alert_titles` (not full body text) and normalized for
     cosine similarity — `search_alerts` only ever matches on title text.
  - If credentials/network fail, `get_lark_token()` raising will crash startup entirely (no
    per-source isolation for the initial token fetch, though per-doc `httpx.HTTPStatusError`s
    during the loop are caught and skipped).
- **Card selection (`build_search_card`)** picks between three Lark interactive card shapes based
  on similarity thresholds `SHOW_SIM` (0.45) and `MIN_SIM` (0.35): an exact/high-confidence match
  gets `build_alert_card` (full detail + "other matches" below `SHOW_SIM`), a middling match gets
  `build_list_card` (title list only, no detail shown), and nothing above `MIN_SIM` gets
  `build_none_card`.
- Lark credentials (`LARK_APP_ID`, `LARK_APP_SECRET`) and any other env vars are loaded via
  `load_dotenv()` from a local `.env` (git-ignored, not committed — see `.env` for the current
  values, `.env.example` for the expected keys including `WIKI_NODE_TOKENS`).

## Commands

```bash
# Activate the venv (Python 3.14)
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the API locally (also picks up .env via load_dotenv())
uvicorn app:app --reload --port 8000
```

There is no configured test runner (no pytest config, no CI). `test_app.py` and `simple_test.py`
are standalone FastAPI health-check apps, not actual tests of `app.py` — do not treat them as a
test suite covering the real application.

## Notes for future work

- `requirements.txt` lists `lark-parser` (an unrelated Python parsing-library package — don't
  confuse it with the Lark/Feishu messaging platform) and `faiss-cpu`, but `app.py` does not
  actually use FAISS; similarity search is a plain `numpy.dot` over normalized embeddings.
  `requirements.txt` is also currently missing `python-dotenv`, which `app.py` imports directly.
- `alertdocs/` and `feedback/` are both currently empty directories.
- The Lark Wiki/Docx sources indexed at startup are hardcoded inside `lifespan` (`lark_docs`
  dict) rather than driven by the `WIKI_NODE_TOKENS` env var described in `.env.example` — the two
  are not currently wired together.
