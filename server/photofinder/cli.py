"""Command line entry point: index, search, serve, bench."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import config, db
from .bootstrap import open_library
from .embedder import get_embedder
from .ingest import IngestManager


def _fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return ""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def cmd_index(args: argparse.Namespace) -> int:
    config.ensure_dirs()
    db.init()
    paths = []
    for raw in args.paths:
        path = Path(raw).expanduser()
        if not path.exists():
            print(f"skip (not found): {path}", file=sys.stderr)
            continue
        paths.append(str(path.resolve()))
    if not paths:
        print("nothing to index", file=sys.stderr)
        return 2

    print(f"loading {config.MODEL_NAME}/{config.MODEL_PRETRAINED} ...")
    embedder = get_embedder()
    index = open_library(embedder)

    manager = IngestManager(embedder=embedder, index=index)
    manager.start()
    for path in paths:
        manager.enqueue(path, full=args.full)

    last_line = ""
    try:
        while True:
            status = manager.status()
            if status["state"] in ("done", "error", "cancelled") and status["queued"] == 0:
                break
            if status["state"] in ("scanning", "indexing"):
                line = (
                    f"\r{status['state']:<9} {status['phase'] or '':<10} "
                    f"{status['done']}/{status['total'] or '?'} "
                    f"new {status['indexed']} skip {status['skipped']} err {status['errors']} "
                    f"{status['rate']:.1f}/s {status['current'] or '':.40}"
                )
                if line != last_line:
                    print(line.ljust(110), end="", flush=True)
                    last_line = line
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\ncancelling ...")
        manager.cancel()
        manager.shutdown()
        return 130

    print()
    status = manager.status()
    print(
        f"{status['state']}: {status['indexed']} indexed, {status['skipped']} unchanged, "
        f"{status['errors']} errors, {status['added_items']} vectors"
    )
    stats = db.stats()
    print(
        f"library: {stats['images']} photos, {stats['videos']} videos, "
        f"{stats['items']} searchable moments"
    )
    if status["message"]:
        print(status["message"])
    manager.shutdown()
    return 0 if status["state"] != "error" else 1


def cmd_search(args: argparse.Namespace) -> int:
    config.ensure_dirs()
    db.init()
    embedder = get_embedder()
    index = open_library(embedder)
    query = " ".join(args.query)
    from .search import search

    result = search(
        query,
        limit=args.limit,
        kind=args.kind,
        index=index,
        embedder=embedder,
        show_all=args.all,
    )
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    if not result["results"]:
        print("no results")
        return 1
    if result["weak_only"]:
        print("no strong match; showing best guesses")
    summary = f'{len(result["results"])} shown'
    if not args.all:
        summary += f' · {result["strong_count"]} strong · {result["weak_count"]} weak hidden'
    print(f'{summary} in {result["took_ms"]:.1f} ms  ({query})')
    for n, item in enumerate(result["results"], 1):
        stamp = f" {_fmt_duration(item['t'])}" if item["kind"] == "video" else ""
        mark = "" if item["strong"] else "  (weak)"
        print(f"{n:3d}. {item['score']:.3f}{mark:<8} {item['kind']:<5}{stamp:<11} {item['path']}")
        for moment in item["moments"][1:]:
            print(f"       also {moment['score']:.3f}  {moment['t']}s")
    print(
        f'timings: embed {result["timings"]["embed_ms"]} ms, '
        f'faiss {result["timings"]["faiss_ms"]} ms, db {result["timings"]["db_ms"]} ms'
    )
    return 0


def cmd_stats(_: argparse.Namespace) -> int:
    config.ensure_dirs()
    db.init()
    stats = db.stats()
    print(json.dumps({**stats, "roots": db.roots()}, indent=2))
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    config.ensure_dirs()
    db.init()
    embedder = get_embedder()
    index = open_library(embedder)
    from .search import benchmark

    queries = args.queries or [
        "a dog running on a beach",
        "screenshot of a terminal",
        "two people at a dinner table",
        "night city street with neon",
        "a whiteboard full of diagrams",
    ]
    result = benchmark(queries, limit=args.limit, runs=args.runs)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f'{result["vectors"]} vectors, {result["runs"]} queries: '
            f'p50 {result["p50_ms"]} ms, p95 {result["p95_ms"]} ms, max {result["max_ms"]} ms'
        )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "photofinder.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


def cmd_desktop(args: argparse.Namespace) -> int:
    from .desktop import run_desktop

    return run_desktop(port=args.port)


def cmd_stop(_: argparse.Namespace) -> int:
    from .desktop import stop_server

    return stop_server()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="photofinder", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index", help="index a folder or file (incremental)")
    p.add_argument("paths", nargs="+")
    p.add_argument("--full", action="store_true", help="re-embed everything, even unchanged files")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("search", help="search the local index")
    p.add_argument("query", nargs="+")
    p.add_argument("-n", "--limit", type=int, default=20)
    p.add_argument("--kind", choices=["image", "video"], default=None)
    p.add_argument("--all", action="store_true", help="include weak matches below the confidence cutoff")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("stats", help="library statistics")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("bench", help="measure search latency")
    p.add_argument("-n", "--limit", type=int, default=60)
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--queries", nargs="*", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("serve", help="run the API + web UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8642)
    p.add_argument("--reload", action="store_true")
    p.add_argument("--log-level", default="info")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("desktop", help="start in the background, open the browser, show a tray icon")
    p.add_argument("--port", type=int, default=None)
    p.set_defaults(func=cmd_desktop)

    p = sub.add_parser("stop", help="stop a desktop server started with 'desktop'")
    p.set_defaults(func=cmd_stop)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
