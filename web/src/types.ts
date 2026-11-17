export type MediaKind = "image" | "video";
export type KindFilter = "all" | MediaKind;

export interface Moment {
  item_id: number;
  t: number | null;
  score: number;
}

export interface MediaItem {
  media_id: number;
  item_id: number;
  path: string;
  name: string;
  dir?: string;
  kind: MediaKind;
  t: number | null;
  duration: number | null;
  score?: number;
  strong?: boolean;
  text_score?: number;
  thumb: string;
  media_url: string;
  width?: number | null;
  height?: number | null;
  taken_at?: string | null;
  mtime?: number | null;
  moments?: Moment[];
}

export interface SearchTimings {
  embed_ms: number;
  faiss_ms: number;
  calibrate_ms: number;
  lexical_ms: number;
  db_ms: number;
  group_ms: number;
}

export interface SearchResponse {
  query: string;
  results: MediaItem[];
  candidates: number;
  strong_count: number;
  weak_count: number;
  weak_only: boolean;
  cutoff: number | null;
  cutoff_z: number | null;
  show_all: boolean;
  took_ms: number;
  timings: SearchTimings;
}

export interface RecentResponse {
  results: MediaItem[];
}

export interface LibraryStats {
  images: number;
  videos: number;
  items: number;
  errors: number;
  bytes: number;
  last_indexed: string | null;
  model: string;
  roots: string[];
  vectors: number;
}

export interface Health {
  ok: boolean;
  boot: "loading" | "ready" | "error";
  error: string | null;
  model: string;
  pretrained: string;
  dim: number | null;
  vectors: number;
  ocr?: {
    engine: string | null;
    error?: string | null;
    documents?: number;
    processed?: number;
  };
  data_dir: string;
}

export interface IngestStatus {
  state: "idle" | "scanning" | "indexing" | "done" | "error" | "cancelled";
  root: string | null;
  phase: string | null;
  total: number;
  done: number;
  indexed: number;
  skipped: number;
  errors: number;
  added_items: number;
  current: string | null;
  message: string | null;
  started_at: number | null;
  finished_at: number | null;
  queued: number;
  rate: number;
  eta_seconds: number | null;
}

export interface StatusResponse {
  ingest: IngestStatus;
  boot: string;
  error: string | null;
}
