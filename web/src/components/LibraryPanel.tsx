import { useEffect, useState } from "react";
import type { Health, IngestStatus, LibraryStats } from "../types";
import { compact, formatBytes } from "../api";
import {
  AlertIcon,
  CheckIcon,
  CloseIcon,
  FolderIcon,
  PlusIcon,
  RefreshIcon,
  TrashIcon,
} from "./Icons";

interface Props {
  open: boolean;
  onClose: () => void;
  health: Health | null;
  stats: LibraryStats | null;
  ingest: IngestStatus | null;
  onAddPath: (path: string) => Promise<void>;
  onRescan: (full: boolean) => Promise<void>;
  onCancel: () => Promise<void>;
  onRemoveRoot: (path: string) => Promise<void>;
}

export function LibraryPanel({
  open,
  onClose,
  health,
  stats,
  ingest,
  onAddPath,
  onRescan,
  onCancel,
  onRemoveRoot,
}: Props) {
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) setError(null);
  }, [open]);

  if (!open) return null;

  const running = ingest != null && (ingest.state === "scanning" || ingest.state === "indexing" || ingest.queued > 0);
  const percent = ingest && ingest.total > 0 ? Math.min(100, (ingest.done / ingest.total) * 100) : 0;

  const add = async () => {
    const value = path.trim();
    if (!value || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onAddPath(value);
      setPath("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const rescan = async (full: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await onRescan(full);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (root: string) => {
    const ok = window.confirm(`Remove this folder from the library?\n\n${root}\n\nYour files are not deleted.`);
    if (!ok) return;
    setBusy(true);
    try {
      await onRemoveRoot(root);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="drawer-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <aside className="drawer" aria-label="Library settings">
        <header className="drawer-head">
          <div>
            <h2>Library</h2>
            <p>Everything stays on this machine.</p>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            <CloseIcon size={17} />
          </button>
        </header>

        <div className="drawer-body">
          {error && (
            <div className="notice notice-error">
              <AlertIcon size={15} />
              <span>{error}</span>
            </div>
          )}

          {ingest && running && (
            <section className="index-live">
              <div className="index-live-head">
                <span className="pulse-dot" />
                <strong>
                  {ingest.state === "scanning" ? "Scanning" : "Embedding"}
                  {ingest.phase === "video" ? " video" : ""}
                </strong>
                {ingest.total > 0 && (
                  <span className="mono index-live-count">
                    {compact(ingest.done)} / {compact(ingest.total)}
                  </span>
                )}
              </div>
              <div className={`progress ${percent === 0 ? "is-indeterminate" : ""}`}>
                <span style={{ width: `${Math.max(percent, 3)}%` }} />
              </div>
              <div className="index-live-meta">
                <span className="ellipsis">{ingest.current || ingest.root}</span>
                <span className="mono">{ingest.rate.toFixed(1)}/s</span>
              </div>
              <div className="index-live-stats">
                <span>{compact(ingest.indexed)} new</span>
                <span>{compact(ingest.skipped)} unchanged</span>
                <span className={ingest.errors ? "text-warn" : ""}>{ingest.errors} errors</span>
                <span>{compact(ingest.added_items)} moments</span>
              </div>
              <button type="button" className="btn btn-ghost btn-wide" onClick={() => void onCancel()}>
                Stop indexing
              </button>
            </section>
          )}

          {ingest && ingest.state === "done" && !running && ingest.finished_at && (
            <div className="notice notice-ok">
              <CheckIcon size={15} />
              <span>
                Last scan finished · {compact(ingest.indexed)} new, {compact(ingest.skipped)} unchanged
                {ingest.errors ? `, ${ingest.errors} errors` : ""}
              </span>
            </div>
          )}

          {ingest && ingest.state === "error" && (
            <div className="notice notice-error">
              <AlertIcon size={15} />
              <span>{ingest.message || "Indexing failed."}</span>
            </div>
          )}

          <section>
            <h3 className="section-title">Add a folder</h3>
            <div className="path-row">
              <div className="path-input">
                <FolderIcon size={15} />
                <input
                  value={path}
                  onChange={(event) => setPath(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void add();
                  }}
                  placeholder="C:\Users\you\Pictures"
                  spellCheck={false}
                  disabled={busy}
                />
              </div>
              <button type="button" className="btn btn-solid" onClick={() => void add()} disabled={busy || !path.trim()}>
                <PlusIcon size={15} />
                <span>Index</span>
              </button>
            </div>
            <p className="hint">
              Subfolders are included. New and changed files are embedded; unchanged files are skipped.
              Videos are sampled into searchable moments.
            </p>
          </section>

          <section>
            <div className="section-row">
              <h3 className="section-title">Folders</h3>
              <div className="row-actions">
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => void rescan(false)} disabled={busy || running || !stats?.roots.length}>
                  <RefreshIcon size={13} />
                  <span>Rescan</span>
                </button>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => {
                    if (window.confirm("Re-embed every file with the current model? This can take a while.")) {
                      void rescan(true);
                    }
                  }}
                  disabled={busy || running || !stats?.roots.length}
                >
                  Rebuild
                </button>
              </div>
            </div>
            <ul className="roots">
              {(stats?.roots ?? []).map((root) => (
                <li key={root}>
                  <FolderIcon size={15} />
                  <span className="ellipsis" title={root}>
                    {root}
                  </span>
                  <button
                    type="button"
                    className="icon-btn tiny"
                    onClick={() => void remove(root)}
                    disabled={busy || running}
                    aria-label={`Remove ${root}`}
                  >
                    <TrashIcon size={14} />
                  </button>
                </li>
              ))}
              {!stats?.roots.length && <li className="empty-row">No folders yet.</li>}
            </ul>
          </section>

          <section>
            <h3 className="section-title">Index</h3>
            <div className="stat-grid">
              <div className="stat">
                <span className="stat-value">{compact(stats?.images ?? 0)}</span>
                <span className="stat-label">photos</span>
              </div>
              <div className="stat">
                <span className="stat-value">{compact(stats?.videos ?? 0)}</span>
                <span className="stat-label">clips</span>
              </div>
              <div className="stat">
                <span className="stat-value">{compact(stats?.items ?? 0)}</span>
                <span className="stat-label">moments</span>
              </div>
            </div>
            <dl className="facts">
              <div>
                <dt>Embeddings</dt>
                <dd className="mono">{health ? `${health.model} · ${health.pretrained}` : "—"}</dd>
              </div>
              <div>
                <dt>Vectors</dt>
                <dd className="mono">{compact(health?.vectors ?? 0)}</dd>
              </div>
              <div>
                <dt>Media size</dt>
                <dd className="mono">{formatBytes(stats?.bytes ?? 0)}</dd>
              </div>
              <div>
                <dt>Errors</dt>
                <dd className={`mono ${stats?.errors ? "text-warn" : ""}`}>{stats?.errors ?? 0}</dd>
              </div>
            </dl>
          </section>
        </div>
      </aside>
    </div>
  );
}
