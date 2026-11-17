import { useState } from "react";
import type { MediaItem } from "../types";
import { formatTime } from "../api";
import { FilmIcon, ImageIcon, PlayIcon } from "./Icons";

interface Props {
  items: MediaItem[];
  selected: number;
  onSelect: (index: number) => void;
  onOpen: (index: number) => void;
  loading: boolean;
}

function Tile({
  item,
  index,
  selected,
  eager,
  onSelect,
  onOpen,
}: {
  item: MediaItem;
  index: number;
  selected: boolean;
  eager: boolean;
  onSelect: (index: number) => void;
  onOpen: (index: number) => void;
}) {
  const [broken, setBroken] = useState(false);
  const isVideo = item.kind === "video";

  return (
    <button
      type="button"
      className={`tile ${selected ? "is-selected" : ""} ${item.strong === false ? "is-weak" : ""}`}
      data-index={index}
      data-selected={selected || undefined}
      aria-label={`${item.name}${isVideo ? `, clip at ${formatTime(item.t)}` : ""}`}
      onMouseDown={() => onSelect(index)}
      onClick={() => onOpen(index)}
    >
      {broken ? (
        <span className="tile-broken">
          {isVideo ? <FilmIcon size={22} /> : <ImageIcon size={22} />}
          <span>{item.name}</span>
        </span>
      ) : (
        <img
          src={item.thumb}
          alt=""
          loading={eager ? "eager" : "lazy"}
          decoding="async"
          draggable={false}
          onError={() => setBroken(true)}
        />
      )}

      {isVideo && (
        <span className="tile-play" aria-hidden>
          <PlayIcon size={13} />
        </span>
      )}

      <span className="tile-shade" aria-hidden />
      <span className="tile-caption">
        <span className="tile-name">{item.name}</span>
        {isVideo && item.t != null && <span className="tile-moment">{formatTime(item.t)}</span>}
      </span>

      {isVideo && (
        <span className="badge badge-duration">
          {formatTime(item.duration)}
          {item.t != null && item.t > 0 && <em>· {formatTime(item.t)}</em>}
        </span>
      )}
    </button>
  );
}

export function ResultGrid({ items, selected, onSelect, onOpen, loading }: Props) {
  if (loading && items.length === 0) {
    return (
      <div className="grid" aria-busy>
        {Array.from({ length: 18 }).map((_, i) => (
          <div className="skeleton" key={i} style={{ animationDelay: `${i * 28}ms` }} />
        ))}
      </div>
    );
  }

  return (
    <div className={`grid ${loading ? "is-loading" : ""}`}>
      {items.map((item, i) => (
        <Tile
          key={`${item.item_id}-${i}`}
          item={item}
          index={i}
          selected={selected === i}
          eager={i < 8}
          onSelect={onSelect}
          onOpen={onOpen}
        />
      ))}
    </div>
  );
}
