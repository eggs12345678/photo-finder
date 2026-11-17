import { useCallback, useEffect, useRef, useState } from "react";
import { api, compact, kindLabel, toDisplay } from "./api";
import type {
  Health,
  IngestStatus,
  KindFilter,
  LibraryStats,
  MediaItem,
  SearchResponse,
} from "./types";
import { ResultGrid } from "./components/ResultGrid";
import { Lightbox } from "./components/Lightbox";
import { LibraryPanel } from "./components/LibraryPanel";
import {
  AlertIcon,
  CloseIcon,
  FolderIcon,
  SearchIcon,
  SlidersIcon,
  SparkIcon,
  PlusIcon,
  FilmIcon,
  ImageIcon,
} from "./components/Icons";

const EXAMPLES = [
  "a dog jumping into the sea",
  "whiteboard covered in diagrams",
  "screenshot of a terminal",
  "city street at night in the rain",
  "birthday cake with candles",
  "a red car on a mountain road",
];

export default function App() {
  const initialQuery = new URLSearchParams(window.location.search).get("q") ?? "";
  const [query, setQuery] = useState(initialQuery);
  const [debounced, setDebounced] = useState(initialQuery.trim());
  const [kind, setKind] = useState<KindFilter>("all");

  const [results, setResults] = useState<MediaItem[]>([]);
  const [recent, setRecent] = useState<MediaItem[]>([]);
  const [meta, setMeta] = useState<SearchResponse | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [loading, setLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  const [health, setHealth] = useState<Health | null>(null);
  const [backendDown, setBackendDown] = useState(false);
  const [stats, setStats] = useState<LibraryStats | null>(null);
  const [ingest, setIngest] = useState<IngestStatus | null>(null);
  const [refreshNonce, setRefreshNonce] = useState(0);

  const [selected, setSelected] = useState(-1);
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [toast, setToast] = useState<{ id: number; text: string; error?: boolean } | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);
  const toastTimer = useRef<number | null>(null);
  const lastFinished = useRef<number | null>(null);

  const searching = debounced.trim().length > 0;
  const items = searching ? results : recent;
  const booted = health?.boot === "ready";
  const running = ingest != null && (ingest.state === "scanning" || ingest.state === "indexing" || ingest.queued > 0);

  const notify = useCallback((text: string, error = false) => {
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    setToast({ id: Date.now(), text, error });
    toastTimer.current = window.setTimeout(() => setToast(null), 2600);
  }, []);

  const loadStats = useCallback(async () => {
    try {
      setStats(await api.stats());
    } catch {
      /* API may still be starting */
    }
  }, []);

  // --- health polling -------------------------------------------------------
  useEffect(() => {
    let alive = true;
    let timer = 0;
    const tick = async () => {
      try {
        const next = await api.health();
        if (!alive) return;
        setHealth(next);
        setBackendDown(false);
        timer = window.setTimeout(tick, next.ok ? 20000 : 2500);
      } catch {
        if (!alive) return;
        setBackendDown(true);
        setHealth(null);
        timer = window.setTimeout(tick, 2000);
      }
    };
    void tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, []);

  // --- stats ----------------------------------------------------------------
  useEffect(() => {
    void loadStats();
  }, [loadStats, health?.ok, refreshNonce]);

  // --- ingest status --------------------------------------------------------
  useEffect(() => {
    let alive = true;
    let timer = 0;
    const tick = async () => {
      try {
        const state = await api.status();
        if (!alive) return;
        setIngest(state.ingest);
        const active =
          state.ingest.state === "scanning" ||
          state.ingest.state === "indexing" ||
          state.ingest.queued > 0;
        timer = window.setTimeout(tick, active ? 900 : 6000);
      } catch {
        if (alive) timer = window.setTimeout(tick, 3000);
      }
    };
    void tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    if (!ingest) return;
    if (ingest.state === "done" && ingest.finished_at && ingest.finished_at !== lastFinished.current) {
      lastFinished.current = ingest.finished_at;
      setRefreshNonce((n) => n + 1);
    }
  }, [ingest]);

  // --- search ---------------------------------------------------------------
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(query), 80);
    return () => window.clearTimeout(id);
  }, [query]);

  // A new query or type filter starts strict again.
  useEffect(() => {
    setShowAll(false);
  }, [debounced, kind]);

  useEffect(() => {
    const q = debounced.trim();
    if (!q) {
      setResults([]);
      setMeta(null);
      setSearchError(null);
      setLoading(false);
      return;
    }
    if (!booted) return;
    const controller = new AbortController();
    setLoading(true);
    window.scrollTo({ top: 0, behavior: "auto" });
    api
      .search(q, kind, 60, showAll, controller.signal)
      .then((response) => {
        setResults(response.results);
        setMeta(response);
        setSearchError(null);
        setSelected(-1);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setSearchError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [debounced, kind, showAll, booted, refreshNonce]);

  // --- recent ---------------------------------------------------------------
  useEffect(() => {
    if (!booted || searching) return;
    let alive = true;
    api
      .recent(42, kind)
      .then((response) => {
        if (alive) setRecent(response.results.map(toDisplay));
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [booted, searching, kind, refreshNonce]);

  // --- URL state ------------------------------------------------------------
  useEffect(() => {
    const url = query.trim() ? `?q=${encodeURIComponent(query)}` : window.location.pathname;
    window.history.replaceState(null, "", url);
  }, [query]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    document.body.classList.toggle("no-scroll", openIndex !== null || panelOpen);
  }, [openIndex, panelOpen]);

  useEffect(() => {
    if (selected < 0) return;
    document.querySelector(`[data-index="${selected}"]`)?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  // --- actions --------------------------------------------------------------
  const openItem = useCallback(
    (index: number) => {
      if (index >= 0 && index < items.length) setOpenIndex(index);
    },
    [items.length],
  );

  const reveal = useCallback(
    async (item: MediaItem) => {
      try {
        await api.reveal(item.path);
        notify("Shown in Explorer");
      } catch (error) {
        notify(error instanceof Error ? error.message : "Could not reveal file", true);
      }
    },
    [notify],
  );

  const openFile = useCallback(
    async (item: MediaItem) => {
      try {
        await api.open(item.path);
        notify("Opened in the default app");
      } catch (error) {
        notify(error instanceof Error ? error.message : "Could not open file", true);
      }
    },
    [notify],
  );

  const copyPath = useCallback(
    async (item: MediaItem) => {
      try {
        await navigator.clipboard.writeText(item.path);
        notify("Path copied");
      } catch {
        notify("Clipboard is unavailable", true);
      }
    },
    [notify],
  );

  const addPath = useCallback(
    async (path: string) => {
      await api.indexPath(path);
      notify("Indexing started");
      setRefreshNonce((n) => n + 1);
    },
    [notify],
  );

  const rescan = useCallback(
    async (full: boolean) => {
      const result = await api.rescan(full);
      notify(result.queued ? `Rescanning ${result.queued} folder${result.queued === 1 ? "" : "s"}` : "Already up to date");
    },
    [notify],
  );

  const cancel = useCallback(async () => {
    await api.cancel();
    notify("Stopping after the current file");
  }, [notify]);

  const removeRoot = useCallback(
    async (path: string) => {
      const result = await api.removeRoot(path);
      notify(`Removed ${result.removed} file${result.removed === 1 ? "" : "s"} from the index`);
      setRefreshNonce((n) => n + 1);
    },
    [notify],
  );

  // --- keyboard -------------------------------------------------------------
  useEffect(() => {
    const columns = () => {
      const grid = document.querySelector(".grid");
      if (!grid) return 4;
      const template = getComputedStyle(grid).gridTemplateColumns;
      return Math.max(1, template.split(" ").filter(Boolean).length);
    };
    const move = (delta: number) => {
      setSelected((current) => {
        if (items.length === 0) return -1;
        if (current < 0) return delta > 0 ? 0 : items.length - 1;
        return Math.max(0, Math.min(items.length - 1, current + delta));
      });
    };

    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing =
        target != null &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);

      if (openIndex !== null) {
        if (event.key === "Escape") {
          event.preventDefault();
          setOpenIndex(null);
        } else if (event.key === "ArrowRight") {
          event.preventDefault();
          setOpenIndex(Math.min(openIndex + 1, items.length - 1));
        } else if (event.key === "ArrowLeft") {
          event.preventDefault();
          setOpenIndex(Math.max(openIndex - 1, 0));
        } else if (!typing && event.key.toLowerCase() === "o" && items[openIndex]) {
          void openFile(items[openIndex]);
        }
        return;
      }

      if (panelOpen) {
        if (event.key === "Escape") {
          event.preventDefault();
          setPanelOpen(false);
        }
        return;
      }

      if (event.key === "/" && !typing) {
        event.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
        return;
      }

      if (event.key === "Escape") {
        if (selected >= 0) setSelected(-1);
        else if (query) {
          setQuery("");
          inputRef.current?.focus();
        } else if (typing) target?.blur();
        return;
      }

      if (items.length === 0) return;

      if (event.key === "ArrowDown") {
        event.preventDefault();
        move(columns());
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        move(-columns());
      } else if (event.key === "ArrowRight" && (!typing || selected >= 0)) {
        event.preventDefault();
        move(1);
      } else if (event.key === "ArrowLeft" && (!typing || selected >= 0)) {
        event.preventDefault();
        move(-1);
      } else if (event.key === "Enter") {
        event.preventDefault();
        openItem(selected >= 0 ? selected : 0);
      } else if (event.key === "Home") {
        event.preventDefault();
        setSelected(0);
      } else if (event.key === "End") {
        event.preventDefault();
        setSelected(items.length - 1);
      }
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, openIndex, selected, panelOpen, query, openItem, openFile]);

  // --- render ---------------------------------------------------------------
  const busy = running;
  const latency = meta && meta.query === debounced.trim() ? meta.took_ms : null;
  const hasLibrary = (stats?.images ?? 0) + (stats?.videos ?? 0) > 0;
  const showBoot = !health || !booted;
  const hero = !query.trim();
  const currentItem = openIndex !== null ? items[openIndex] : null;

  return (
    <div className="app">
      <header className="topbar">
        <button
          type="button"
          className="brand"
          onClick={() => {
            setQuery("");
            setKind("all");
            inputRef.current?.focus();
          }}
        >
          <span className="brand-mark" aria-hidden />
          <span className="brand-name">photo<span>·</span>finder</span>
        </button>

        <div className="topbar-center">
          {busy && ingest ? (
            <button type="button" className="status-chip is-active" onClick={() => setPanelOpen(true)}>
              <span className="spinner small" />
              <span>
                {ingest.state === "scanning" ? "Scanning" : "Indexing"}
                {ingest.total > 0 ? ` ${compact(ingest.done)}/${compact(ingest.total)}` : ""}
              </span>
              {ingest.current && <em>{ingest.current}</em>}
            </button>
          ) : (
            <span className="status-chip">
              <span className={`status-dot ${booted ? "ok" : ""}`} />
              {booted
                ? `${compact(stats?.images ?? 0)} photos · ${compact(stats?.videos ?? 0)} clips · ${compact(stats?.items ?? 0)} moments`
                : "starting…"}
            </span>
          )}
        </div>

        <div className="topbar-actions">
          <button type="button" className="btn btn-ghost hide-sm" onClick={() => setPanelOpen(true)}>
            <PlusIcon size={15} />
            <span>Add folder</span>
          </button>
          <button
            type="button"
            className="icon-btn"
            onClick={() => setPanelOpen(true)}
            aria-label="Library settings"
            title="Library"
          >
            <SlidersIcon size={17} />
          </button>
        </div>
      </header>

      <main className="main">
        <section className={`search-zone ${hero ? "is-hero" : ""}`}>
          {hero && (
            <div className="hero-copy">
              <h1>
                Type what you <span>remember.</span>
              </h1>
              <p>
                Every photo and every clip moment, searchable by description. No tags, no folders,
                no scrolling.
              </p>
            </div>
          )}

          <div className={`search-box ${loading ? "is-busy" : ""}`}>
            <SearchIcon size={hero ? 21 : 18} />
            <input
              ref={inputRef}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setSelected(-1);
              }}
              placeholder={
                hero
                  ? "a photo of…"
                  : "Describe the photo or clip…"
              }
              aria-label="Search photos and clips"
              spellCheck={false}
              autoComplete="off"
            />
            {query ? (
              <button
                type="button"
                className="clear-btn"
                onClick={() => {
                  setQuery("");
                  inputRef.current?.focus();
                }}
                aria-label="Clear search"
              >
                <CloseIcon size={15} />
              </button>
            ) : (
              <kbd className="slash-hint">/</kbd>
            )}
            {loading && <span className="spinner" aria-hidden />}
          </div>

          {hero && (
            <div className="example-row">
              {EXAMPLES.map((example) => (
                <button
                  type="button"
                  key={example}
                  className="example-chip"
                  onClick={() => {
                    setQuery(example);
                    inputRef.current?.focus();
                  }}
                >
                  {example}
                </button>
              ))}
            </div>
          )}
        </section>

        <section className="results-section">
          <div className="results-bar">
            <div className="filters" role="tablist" aria-label="Filter by type">
              {(["all", "image", "video"] as KindFilter[]).map((value) => (
                <button
                  type="button"
                  key={value}
                  role="tab"
                  aria-selected={kind === value}
                  className={`filter ${kind === value ? "is-active" : ""}`}
                  onClick={() => {
                    setKind(value);
                    setSelected(-1);
                  }}
                >
                  {value === "all" && <SparkIcon size={13} />}
                  {value === "image" && <ImageIcon size={13} />}
                  {value === "video" && <FilmIcon size={13} />}
                  <span>{kindLabel(value)}</span>
                  {value !== "all" && (
                    <em>{compact(value === "image" ? stats?.images ?? 0 : stats?.videos ?? 0)}</em>
                  )}
                </button>
              ))}
            </div>

            <div className="results-meta">
              {searching ? (
                <>
                  <span className="result-count">
                    {loading && results.length === 0
                      ? "Searching…"
                      : !meta
                        ? `${results.length} ${results.length === 1 ? "match" : "matches"}`
                        : showAll
                          ? `${results.length} matches · ${meta.weak_count} weak`
                          : meta.weak_only
                            ? "No strong match"
                            : `${meta.strong_count} strong ${
                                meta.strong_count === 1 ? "match" : "matches"
                              }`}
                  </span>
                  {meta && meta.weak_count > 0 && (
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => setShowAll((value) => !value)}
                    >
                      {showAll
                        ? "Strong only"
                        : `Show all ${meta.strong_count + meta.weak_count}`}
                    </button>
                  )}
                  {latency != null && (
                    <span className="latency" title={meta ? `embed ${meta.timings.embed_ms} ms · faiss ${meta.timings.faiss_ms} ms · text ${meta.timings.lexical_ms} ms · db ${meta.timings.db_ms} ms` : undefined}>
                      {latency < 10 ? latency.toFixed(1) : Math.round(latency)} ms
                    </span>
                  )}
                </>
              ) : (
                <span className="result-count">
                  {hasLibrary ? "Recently indexed" : "Your library"}
                </span>
              )}
            </div>
          </div>

          {searchError && (
            <div className="notice notice-error wide">
              <AlertIcon size={15} />
              <span>{searchError}</span>
            </div>
          )}

          {searching && meta?.weak_only && !showAll && !searchError && (
            <div className="notice notice-weak wide">
              <AlertIcon size={15} />
              <span>
                Nothing clears the confidence bar. These {results.length} are the closest guesses —
                try different words before trusting them.
              </span>
            </div>
          )}

          {!hasLibrary && booted && !searching && (
            <div className="empty-card">
              <div className="empty-art" aria-hidden>
                <span className="empty-tile t1" />
                <span className="empty-tile t2" />
                <span className="empty-tile t3" />
              </div>
              <h2>Point it at your photos and clips</h2>
              <p>
                Add a folder and photo-finder embeds every image locally with CLIP. Videos are
                sampled into moments you can search by description.
              </p>
              <button type="button" className="btn btn-solid" onClick={() => setPanelOpen(true)}>
                <FolderIcon size={15} />
                <span>Add your first folder</span>
              </button>
              <span className="empty-hint">Everything is indexed on this machine. Nothing is uploaded.</span>
            </div>
          )}

          {hasLibrary && searching && results.length === 0 && !loading && !searchError && (
            <div className="empty-card compact">
              <h2>Nothing matched that description</h2>
              <p>Try fewer words, or name the colours, objects and actions you actually remember.</p>
            </div>
          )}

          {!hasLibrary && booted && searching && (
            <div className="empty-card compact">
              <h2>Your library is empty</h2>
              <p>Add a folder to embed your photos and clips, then search across all of them.</p>
              <button type="button" className="btn btn-solid btn-sm" onClick={() => setPanelOpen(true)}>
                <FolderIcon size={14} />
                <span>Add a folder</span>
              </button>
            </div>
          )}

          {hasLibrary && !searching && recent.length === 0 && (
            <div className="empty-card compact">
              <h2>Nothing indexed for this filter</h2>
              <p>Switch back to All, or add another folder to the library.</p>
            </div>
          )}

          {(hasLibrary || searching) && (items.length > 0 || loading) && (
            <ResultGrid
              items={items}
              selected={selected}
              onSelect={setSelected}
              onOpen={openItem}
              loading={loading}
            />
          )}

          {items.length > 0 && (
            <div className="grid-foot">
              <span>
                {searching
                  ? `Best ${items.length} of ${compact(meta?.candidates ?? 0)} candidates`
                  : `${items.length} recently indexed`}
              </span>
              <span className="mono">
                {health ? `${health.model} · ${compact(stats?.vectors ?? health.vectors)} vectors` : ""}
              </span>
            </div>
          )}
        </section>
      </main>

      {currentItem && openIndex !== null && (
        <Lightbox
          items={items}
          index={openIndex}
          onClose={() => setOpenIndex(null)}
          onNavigate={setOpenIndex}
          onReveal={reveal}
          onOpen={openFile}
          onCopy={copyPath}
        />
      )}

      <LibraryPanel
        open={panelOpen}
        onClose={() => setPanelOpen(false)}
        health={health}
        stats={stats}
        ingest={ingest}
        onAddPath={addPath}
        onRescan={rescan}
        onCancel={cancel}
        onRemoveRoot={removeRoot}
      />

      {toast && (
        <div className={`toast ${toast.error ? "is-error" : ""}`} key={toast.id}>
          {toast.error ? <AlertIcon size={15} /> : <span className="toast-dot" />}
          <span>{toast.text}</span>
        </div>
      )}

      {showBoot && (
        <div className="boot-overlay">
          <div className="boot-card">
            <span className="brand-mark large" aria-hidden />
            {backendDown ? (
              <>
                <h2>Waiting for the local server</h2>
                <p>
                  Start the API, then this screen connects by itself. Run this in{" "}
                  <code>server/</code>:
                </p>                <pre>uv run python -m photofinder serve</pre>
                <span className="boot-status">
                  <span className="spinner small" /> retrying…
                </span>
              </>
            ) : health?.boot === "error" ? (
              <>
                <h2>Model failed to load</h2>
                <p>{health.error}</p>
                <span className="boot-status">Fix the error above, then restart the server.</span>
              </>
            ) : (
              <>
                <h2>Loading CLIP</h2>
                <p>
                  {health?.model ?? "ViT-B-32"} is warming up. The first run downloads the model
                  once; after that it is loaded from disk.
                </p>
                <div className="boot-progress">
                  <span />
                </div>
                <span className="boot-status">Runs entirely on this machine · CPU or GPU</span>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
