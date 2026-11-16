import { useEffect, useRef, useState } from "react";
import type { MediaItem } from "../types";
import { formatDate, formatTime } from "../api";
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  CloseIcon,
  CopyIcon,
  ExternalIcon,
  FilmIcon,
  FolderIcon,
  ImageIcon,
} from "./Icons";

interface Props {
  items: MediaItem[];
  index: number;
  onClose: () => void;
  onNavigate: (index: number) => void;
  onReveal: (item: MediaItem) => void;
  onOpen: (item: MediaItem) => void;
  onCopy: (item: MediaItem) => void;
}

export function Lightbox({ items, index, onClose, onNavigate, onReveal, onOpen, onCopy }: Props) {
  const item = items[index];
  const videoRef = useRef<HTMLVideoElement>(null);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);
  const [videoError, setVideoError] = useState(false);

  useEffect(() => {
    setImageLoaded(false);
    setImageError(false);
    setVideoError(false);
  }, [item?.item_id]);

  useEffect(() => {
    if (!item || item.kind !== "video") return;
    const video = videoRef.current;
    if (!video) return;
    const target = item.t ?? 0;
    const seek = () => {
      try {
        video.currentTime = Math.max(0, target > 0.4 ? target - 0.35 : 0);
      } catch {
        /* metadata not ready yet */
      }
      void video.play().catch(() => undefined);
    };
    if (video.readyState >= 1) seek();
    else video.addEventListener("loadedmetadata", seek, { once: true });
    return () => video.removeEventListener("loadedmetadata", seek);
  }, [item?.item_id, item?.t, item?.kind, item]);

  if (!item) return null;

  const hasPrev = index > 0;
  const hasNext = index < items.length - 1;
  const meta = [
    formatDate(item.taken_at ?? item.mtime),
    item.width && item.height ? `${item.width} × ${item.height}` : null,
    item.kind === "video" ? `found at ${formatTime(item.t)}` : null,
    item.score != null ? `similarity ${item.score.toFixed(3)}` : null,
  ].filter(Boolean);

  const jumpTo = (t: number | null) => {
    if (!videoRef.current || t == null) return;
    videoRef.current.currentTime = Math.max(0, t - 0.35);
    void videoRef.current.play().catch(() => undefined);
  };

  return (
    <div
      className="lightbox"
      role="dialog"
      aria-modal="true"
      aria-label={item.name}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="lb-top">
        <div className="lb-title">
          <span className={`kind-dot ${item.kind}`} />
          <span className="lb-name">{item.name}</span>
        </div>
        <div className="lb-nav">
          <span className="lb-count">
            {index + 1} / {items.length}
          </span>
          <button type="button" className="icon-btn" onClick={() => onNavigate(index - 1)} disabled={!hasPrev} aria-label="Previous">
            <ArrowLeftIcon size={16} />
          </button>
          <button type="button" className="icon-btn" onClick={() => onNavigate(index + 1)} disabled={!hasNext} aria-label="Next">
            <ArrowRightIcon size={16} />
          </button>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            <CloseIcon size={17} />
          </button>
        </div>
      </div>

      <div className="lb-stage" onMouseDown={(event) => event.stopPropagation()}>
        {item.kind === "video" ? (
          <div className="lb-video-wrap">
            {videoError ? (
              <div className="lb-error">
                <FilmIcon size={24} />
                <span>This clip could not be played in the browser.</span>
                <button type="button" className="btn btn-ghost" onClick={() => onOpen(item)}>
                  Open in the default player
                </button>
              </div>
            ) : (
              <video
                key={item.item_id}
                ref={videoRef}
                src={item.media_url}
                controls
                playsInline
                onError={() => setVideoError(true)}
              />
            )}
            {item.moments && item.moments.length > 1 && (
              <div className="lb-moments">
                <span className="lb-moments-label">Matching moments</span>
                {item.moments.map((moment) => (
                  <button
                    type="button"
                    key={moment.item_id}
                    className="moment-chip"
                    onClick={() => jumpTo(moment.t)}
                  >
                    {formatTime(moment.t)}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className={`lb-image-wrap ${imageLoaded ? "is-loaded" : ""}`}>
            {imageError ? (
              <div className="lb-error">
                <ImageIcon size={24} />
                <span>The file is missing or cannot be decoded.</span>
                <button type="button" className="btn btn-ghost" onClick={() => onReveal(item)}>
                  Reveal in file explorer
                </button>
              </div>
            ) : (
              <img
                key={item.item_id}
                src={item.media_url}
                alt={item.name}
                draggable={false}
                onLoad={() => setImageLoaded(true)}
                onError={() => {
                  setImageLoaded(true);
                  setImageError(true);
                }}
              />
            )}
            {!imageLoaded && !imageError && <span className="lb-image-loading" />}
          </div>
        )}
      </div>

      <div className="lb-bottom" onMouseDown={(event) => event.stopPropagation()}>
        <div className="lb-info">
          <div className="lb-meta">{meta.join("  ·  ")}</div>
          <div className="lb-path" title={item.path}>
            {item.dir || item.path}
          </div>
        </div>
        <div className="lb-actions">
          <button type="button" className="btn btn-ghost" onClick={() => onReveal(item)} title="Show in file explorer">
            <FolderIcon size={15} />
            <span>Reveal</span>
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => onCopy(item)} title="Copy file path">
            <CopyIcon size={15} />
            <span>Copy path</span>
          </button>
          <button type="button" className="btn btn-solid" onClick={() => onOpen(item)}>
            <ExternalIcon size={15} />
            <span>{item.kind === "video" ? "Open clip" : "Open photo"}</span>
          </button>
        </div>
      </div>

      <div className="lb-hints" aria-hidden>
        <kbd>←</kbd> <kbd>→</kbd> navigate <span>·</span> <kbd>O</kbd> open <span>·</span>{" "}
        <kbd>Esc</kbd> close
      </div>
    </div>
  );
}
