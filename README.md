# photo-finder

Type what you remember. Find the exact photo or the exact moment in a clip,
without tags, folders or scrolling.

photo-finder runs entirely on your machine. It embeds every photo with CLIP,
samples videos into searchable moments, and serves exact cosine search from a
FAISS index. The React UI is keyboard-first and shows the measured latency of
every query.

```
"a dog jumping into the sea"      → photo, 12 ms
"the whiteboard from sprint planning" → photo, 2 ms (cached query)
"that bit where the rocket lands"  → clip at 0:42, 14 ms
```

## What is in the box

| Part | Tech |
| --- | --- |
| Embeddings | [CLIP](https://github.com/openai/CLIP) via `open_clip` (`ViT-B-32`, LAION-2B by default) |
| Vector search | FAISS `IndexFlatIP` (exact cosine, id-mapped) |
| Metadata + durability | SQLite (paths, EXIF dates, every vector, thumbnails index) |
| Video moments | ffmpeg frame sampling + near-duplicate dropping + timestamps |
| Screenshot text (optional) | Windows OCR (`winsdk`), RapidOCR or Tesseract → SQLite FTS5 |
| API | Python 3.11, FastAPI, Uvicorn |
| UI | React 19, TypeScript, Vite (no UI framework) |

## Quick start (Windows)

Requirements: [uv](https://docs.astral.sh/uv/), Node 20+, and `ffmpeg` +
`ffprobe` on `PATH`.

```powershell
# 1. backend dependencies (Python 3.11)
cd server
uv sync --python 3.11

# optional: find screenshots by the words on screen
uv sync --python 3.11 --extra ocr-windows   # Windows: uses the built-in OCR engine
# or: uv sync --python 3.11 --extra ocr     # RapidOCR (any OS, downloads ONNX models)

# 2. build the web UI once
cd ..\web
npm install
npm run build

# 3. index a folder (incremental; safe to re-run)
cd ..\server
uv run python -m photofinder index "C:\Users\you\Pictures"

# 4. run the app
uv run python -m photofinder serve
```

Open <http://127.0.0.1:8642>. The first search after a cold start loads the CLIP
model (a one-time ~350 MB download); the UI shows a loading screen while that
happens.

Or use the helper:

```powershell
.\start.ps1                     # builds the UI if needed, then serves
.\start.ps1 -Port 9000          # custom port
```

### Development

Two terminals:

```powershell
# terminal 1 — API on :8642
cd server
uv run python -m photofinder serve --reload

# terminal 2 — Vite on :5173 with /api proxied to :8642
cd web
npm run dev
```

Tests (no model download needed):

```powershell
cd server
uv run pytest
```

Changing video sampling settings only affects newly indexed or rebuilt files.
Use the drawer's **Rebuild** (or `index --full`) after changing
`PHOTOFINDER_VIDEO_INTERVAL` / `PHOTOFINDER_VIDEO_MAX_FRAMES`.

## How it works

### Indexing

1. **Photos** are loaded with EXIF orientation applied, resized to the model's
   square input (the full frame is kept — see *Precision* below), thumbnailed to
   512 px, and embedded once. The thumbnail and the original file are served
   separately.
2. **Videos** are probed with `ffprobe` and sampled with ffmpeg — one frame
   every 2 seconds by default, capped at 300 frames per clip for long videos.
   Consecutive frames whose cosine similarity is above `0.982` are dropped, so
   a static scene contributes one moment rather than hundreds.
3. Every searchable item stores its embedding as a BLOB in SQLite and its
   thumbnail on disk. The FAISS index is a **derived cache**: if it is missing,
   stale or the process crashed mid-save, it is rebuilt from SQLite in seconds
   without running CLIP again.
4. The scanner is incremental: files whose size and mtime match are skipped,
   changed files are re-embedded atomically, and files that disappeared are
   removed from the index (their files are never touched).
5. A model fingerprint (`model/pretrained/preprocess`) is stored with the
   library. Change the model or the preprocessing and the app re-embeds
   everything automatically, because the old vectors are no longer comparable.

### Search

1. The text query is embedded with the same CLIP model as the average of three
   prompts (`"{query}"`, `"a screenshot of {query}"`, `"a photo of {query}"`),
   which shares one forward pass and is less sensitive to wording. Repeated
   queries hit a small LRU vector cache.
2. FAISS returns the top candidates by cosine similarity. The search
   over-fetches 12× the requested result count so that grouping many frames of
   the same video still fills the page.
3. If OCR text exists, the query words are also looked up in the full-text
   index. A file scores `max(CLIP, 0.4 × word coverage)`, so a literal match
   (a name, a title, an error message) is found even when the picture looks
   like nothing in particular, while conceptual queries keep their CLIP order.
   Text-only hits outside the FAISS candidates are added to the pool.
4. Candidates are grouped by file: one card per photo/clip, with up to four
   matching timestamps for videos.
5. Each query is calibrated against a random sample of the library: a result is
   **strong** when its similarity is at least `PHOTOFINDER_STRICT_Z` standard
   deviations above the library's typical similarity for that query, or when at
   least `PHOTOFINDER_OCR_STRONG_COVERAGE` of the query words appear in its
   text. Weak matches are hidden by default; if nothing is strong, the three
   closest guesses are shown with a warning.
6. The response includes `strong_count`, `weak_count`, `weak_only`, `cutoff`,
   `text_matches`, and a timing breakdown (`embed`, `faiss`, `calibrate`,
   `db`, `lexical`, `group`) which the UI exposes in the latency pill.

### Precision

The preprocessing and query settings were chosen on a labelled sample of 80
screenshots and checked on held-out queries (tuning and held-out averages):

| Setup | Precision@R | MRR |
| --- | --- | --- |
| CLIP centre crop, raw query (baseline) | 0.40 | 0.61 |
| **Full-frame squash + three-prompt ensemble** | **0.50** | **0.68** |

Centre-cropping a 16:9 screenshot throws away everything outside the middle
~56%; resizing the whole frame to the model's square input keeps every pixel.
The same idea was tested by padding, 2×2/3×3 tiling, `ViT-B-16`, and DataComp
weights; none beat squash + ensemble consistently enough to justify their cost.
The z-score gate keeps about 70% of relevant results while dropping about 84%
of the library.

**Text search** was measured separately on the same 80 screenshots, with
queries made from strings that actually appear in the images (`AP Biology`,
`beabadoobee concerts`, `WMZH radio station`, …):

| Setup | MRR | Recall@10 |
| --- | --- | --- |
| CLIP only | 0.40 | 0.45 |
| **CLIP + OCR (`max(CLIP, 0.4 × coverage)`)** | **1.00** | **1.00** |

Conceptual queries were unchanged at MRR 0.68 on both tuning and held-out
sets. Frequencies are scored with IDF so a query made of common words cannot
look like a text match.

Re-run `index --full` after changing preprocessing or enabling OCR for the
first time; the app also re-OCRs images automatically when OCR becomes
available.

On this machine, with 53 test moments: **p50 1.45 ms, p95 25 ms** across 40
queries (the p95 comes from the first, uncached text encoding of each query).
A cached query is roughly `1–3 ms`. Flat FAISS search is a few milliseconds at
100k vectors and still practical at a million. The OCR lookup adds well under a
millisecond.

## Using the UI

- Start typing anywhere, or press <kbd>/</kbd>.
- <kbd>↓</kbd> selects the first result, arrows move, <kbd>Enter</kbd> opens.
- <kbd>←</kbd> <kbd>→</kbd> navigate inside the lightbox; <kbd>O</kbd> opens the
  file in its default app; <kbd>Esc</kbd> closes/clears.
- Photos open full-size. Clips open in a player already seeked to the matching
  moment, with chips for the other matching moments.
- **Reveal** shows the file in Windows Explorer; **Copy path** puts the absolute
  path on the clipboard.
- Filters: All / Photos / Clips. The library drawer (sliders icon) adds folders,
  shows live indexing progress, rescan/rebuild, and removes folders from the
  index without deleting files.
- Search shows strong matches only. **Show all** includes weak ones (dimmed);
  **Strong only** hides them again. A new query always starts strict. A small
  **text** badge marks images whose on-screen text matched the query.
- Indexing runs in the background; search stays available.

## Configuration

Environment variables, read by the server and CLI:

| Variable | Default | Meaning |
| --- | --- | --- |
| `PHOTOFINDER_DATA` | `server/data` | SQLite DB, FAISS index, thumbnails |
| `PHOTOFINDER_MODEL` | `ViT-B-32` | Any `open_clip` architecture |
| `PHOTOFINDER_PRETRAINED` | `laion2b_s34b_b79k` | Weights tag for that architecture |
| `PHOTOFINDER_DEVICE` | `cpu` | `cuda` if you installed a CUDA PyTorch build |
| `PHOTOFINDER_VIDEO_INTERVAL` | `2.0` | Seconds between sampled video frames |
| `PHOTOFINDER_VIDEO_MAX_FRAMES` | `300` | Cap per clip (interval grows if needed) |
| `PHOTOFINDER_VIDEO_DEDUPE` | `0.982` | Cosine above this = duplicate frame |
| `PHOTOFINDER_BATCH` | `24` | Embedding batch size |
| `PHOTOFINDER_SQUASH` | `1` | Keep the full frame when resizing (0 = CLIP centre crop) |
| `PHOTOFINDER_STRICT_Z` | `1.0` | Standard deviations above library average to count as strong |
| `PHOTOFINDER_CALIBRATION_SAMPLES` | `2048` | Library vectors sampled per query for the cutoff |
| `PHOTOFINDER_OCR` | `auto` | `auto`, `off`, or `windows` / `rapidocr` / `tesseract` |
| `PHOTOFINDER_OCR_LANG` | `en-US` | OCR language; Windows needs that language pack installed |
| `PHOTOFINDER_OCR_MAX_SIDE` | `2600` | Downscale before OCR (larger is slower, smaller loses small text) |
| `PHOTOFINDER_OCR_BOOST` | `0.4` | Similarity a full text match earns (`max(CLIP, boost × coverage)`) |
| `PHOTOFINDER_OCR_STRONG_COVERAGE` | `0.8` | Share of query words that makes a text match strong |
| `PHOTOFINDER_FFMPEG` / `PHOTOFINDER_FFPROBE` | `ffmpeg` / `ffprobe` | Binary paths |

Higher quality / slower alternative:

```powershell
$env:PHOTOFINDER_MODEL="ViT-B-16"
$env:PHOTOFINDER_PRETRAINED="laion2b_s34b_b88k"
uv run python -m photofinder serve
```

Switching the model re-embeds the library on the next start (the UI shows the
scan), because vectors from different models are not comparable.

## CLI

```powershell
uv run python -m photofinder index "D:\Photos" "E:\Clips"   # incremental
uv run python -m photofinder index "D:\Photos" --full       # re-embed everything
uv run python -m photofinder search "a cat on a keyboard"
uv run python -m photofinder search "sunset" --kind video -n 5 --json
uv run python -m photofinder search "terminal with white text" --all   # include weak
uv run python -m photofinder bench                          # latency percentiles
uv run python -m photofinder stats
uv run python -m photofinder serve --host 127.0.0.1 --port 8642
```

`index` prints progress and a summary; `stats` shows counts and roots; `bench`
runs a set of queries against the current index.

## API

The API is local and unauthenticated by design (bind it to `127.0.0.1` only).

| Endpoint | Purpose |
| --- | --- |
| `GET /api/search?q=…&limit=60&kind=image\|video&all=false` | semantic search with timings and strong/weak counts |
| `GET /api/recent?limit=42&kind=…` | recently indexed media |
| `GET /api/stats` | counts, roots, vectors, size |
| `GET /api/health` | boot state, model, vector count, OCR engine and text count |
| `GET /api/status` | indexing progress |
| `POST /api/index` `{ "path": "...", "full": false }` | add/refresh a folder or file |
| `POST /api/rescan?full=false` | rescan every root |
| `POST /api/cancel` | stop after the current file |
| `DELETE /api/roots?path=…` | remove a root from the index (files stay) |
| `GET /api/thumb/{item_id}` | cached JPEG thumbnail |
| `GET /api/media/{media_id}` | original file, range-capable |
| `POST /api/reveal` / `POST /api/open` `{ "path": "..." }` | Explorer / default app |

Interactive docs: <http://127.0.0.1:8642/docs>.

## Supported files

Photos: `jpg jpeg png webp bmp gif tif tiff heic heif avif` (HEIC/AVIF through
`pillow-heif`). Videos: `mp4 mov m4v mkv webm avi wmv mpg mpeg`.

Hidden folders, `.git`, `node_modules`, recycle bins and the app's own data
folder are skipped.

## Privacy

- No network calls at search or index time; everything is local. The only
  network use is the one-time model download from Hugging Face.
- No analytics, no cloud, no image uploads. Phones are not involved.
- `.gitignore` excludes `server/data/`, so the SQLite database, embeddings and
  thumbnails do not end up in git.

## Limits and good to know

- CLIP is good at objects, scenes, colours, actions and vibes; it is not OCR or
  a face-recognition system. People are described rather than identified.
- OCR is optional and covers photos/screenshots, not video frames yet. It reads
  the built-in Windows engine (or RapidOCR/Tesseract); accuracy depends on
  resolution, fonts and language. Enabling it later re-OCRs images on the next
  scan without re-embedding them.
- OCR costs roughly 15–60 ms per screenshot on a modern desktop. A 7,000-image
  screenshot library adds a few minutes to the first full scan, not to searches.
- Queries work best in English (the default model was trained on English
  captions). Shorter, concrete descriptions outperform long stories.
- Video moments have the granularity of the sampling interval (2 s by default).
  Lower `PHOTOFINDER_VIDEO_INTERVAL` for fast-cut footage, at the cost of more
  vectors and longer indexing.
- CPU indexing is roughly 20–30 images/second on a modern desktop. A CUDA
  PyTorch build plus `PHOTOFINDER_DEVICE=cuda` is much faster.
- A million moments is about 2 GB of RAM for the flat index. That is still an
  exact search; if you go far beyond that, switch to an IVF/HNSW index.
- There is no live filesystem watcher yet. Folders are rescanned on server
  start, with the Rescan button, or by re-running `index`.
- Deleted Search results are removed by rescanning the parent folder. Removing
  a folder in the drawer removes only index entries.

## Model and third-party licences

This project does not grant you a licence to the models or media you index.

- The default weights (`laion/CLIP-ViT-B-32-laion2B-s34B-b79K`) are published
  with a **MIT** licence tag on Hugging Face, but the model card describes the
  model as a research output and notes that deployed use is out of scope. The
  training dataset is uncurated LAION-2B. **Verify the current licence and
  terms yourself before commercial use.**
- `ViT-B-32` with `pretrained="openai"` uses OpenAI's CLIP weights instead;
  check their terms as well.
- Code licences: `open_clip` MIT, PyTorch BSD-3, FAISS MIT, FastAPI MIT,
  Pillow HPND, React MIT, Vite MIT. ffmpeg is a separate system dependency;
  its build/licence depends on how it was compiled (LGPL/GPL).
- Optional OCR: `winsdk` is MIT and calls the OCR engine included with Windows;
  `rapidocr-onnxruntime` is Apache-2.0 and bundles PP-OCR ONNX models (also
  Apache-2.0 at the time of writing); Tesseract is Apache-2.0. Check the model
  and language-pack terms before commercial use.

CLIP can reproduce biases present in its training data. Do not use it for
surveillance or identity decisions.

## Project layout

```
server/
  photofinder/
    api.py        FastAPI routes, boot, static UI
    bootstrap.py  open library, rebuild FAISS from SQLite, model fingerprint
    cli.py        index / search / serve / bench / stats
    config.py     paths and tuning knobs
    db.py         SQLite schema and queries
    embedder.py   CLIP image + text embeddings
    index.py      FAISS wrapper (add, remove, search, rebuild, atomic save)
    ingest.py     scanner, EXIF, thumbnails, OCR, video sampling, progress
    ocr.py        optional Windows / RapidOCR / Tesseract text extraction
    search.py     query → confidence-gated, grouped results with timings
web/
  src/
    App.tsx               search, keyboard model, polling, URL state
    components/ResultGrid.tsx
    components/Lightbox.tsx
    components/LibraryPanel.tsx
    styles.css
```

Data directory (`server/data/`):

```
library.db     metadata + every embedding (source of truth)
index.faiss    FAISS cache, safe to delete
thumbs/        JPEG thumbnails, content-addressed
```
