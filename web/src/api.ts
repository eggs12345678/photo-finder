import type {
  Health,
  KindFilter,
  LibraryStats,
  MediaItem,
  RecentResponse,
  SearchResponse,
  StatusResponse,
} from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* plain body */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => request<Health>("/api/health"),

  stats: () => request<LibraryStats>("/api/stats"),

  status: () => request<StatusResponse>("/api/status"),

  search: (query: string, kind: KindFilter, limit = 60, signal?: AbortSignal) => {
    const params = new URLSearchParams({ q: query, limit: String(limit) });
    if (kind !== "all") params.set("kind", kind);
    return request<SearchResponse>(`/api/search?${params}`, { signal });
  },

  recent: (limit = 30, kind: KindFilter = "all") => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (kind !== "all") params.set("kind", kind);
    return request<RecentResponse>(`/api/recent?${params}`);
  },

  indexPath: (path: string, full = false) =>
    request<{ queued: boolean }>("/api/index", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, full }),
    }),

  rescan: (full = false) =>
    request<{ queued: number }>(`/api/rescan?full=${full}`, { method: "POST" }),

  cancel: () => request<unknown>("/api/cancel", { method: "POST" }),

  removeRoot: (path: string) =>
    request<{ removed: number }>(`/api/roots?path=${encodeURIComponent(path)}`, {
      method: "DELETE",
    }),

  reveal: (path: string) =>
    request<{ ok: true }>("/api/reveal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    }),

  open: (path: string) =>
    request<{ ok: true }>("/api/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    }),
};

export function kindLabel(kind: KindFilter): string {
  return kind === "all" ? "All" : kind === "image" ? "Photos" : "Clips";
}

export function toDisplay(item: MediaItem): MediaItem {
  return { ...item, dir: item.dir ?? item.path.replace(/[\\/][^\\/]+$/, "") };
}

export function formatTime(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "";
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}:${String(m % 60).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function formatDate(value?: string | number | null): string {
  if (value == null) return "";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(date);
}

export function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function compact(value: number): string {
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(
    value,
  );
}
