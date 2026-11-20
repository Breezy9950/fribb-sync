#!/usr/bin/env python3
"""Cumulative Fribb anime-mapping sync.

This script is a pure function of its inputs and performs NO network I/O.
The GitHub Actions workflow downloads the upstream Fribb dataset and the
previous release assets; this script only merges what it is given and writes
the published outputs.

Merge rules (see README.md):
  * Key every entry by ``anilist_id``; skip entries where it is null or missing.
  * Start from the old cumulative store, never from fresh.
  * Upserts are fresh entries that are new or differ from old (whole-object
    comparison; any field difference counts as a change).
  * Overrides always win and are applied last.
  * Entries present in old but absent from fresh are NEVER removed. They are
    reported as ``dropped_ids`` on stderr for monitoring only.

Inputs and outputs are explicit paths, so the script can be exercised locally
with small synthetic fixtures and zero network calls.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

EXIT_OK = 0


def eprint(*args):
    print(*args, file=sys.stderr)


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path, obj, indent=2, compact=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        if compact:
            json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, fh, ensure_ascii=False, indent=indent)
            fh.write("\n")


def entry_id(entry):
    """Return the entry's anilist_id as a string, or None when absent/null."""
    aid = entry.get("anilist_id")
    if aid is None:
        return None
    return str(aid)


def index_by_anilist(entries):
    """Build ``{anilist_id_str: entry}`` from a list, skipping null/missing ids."""
    indexed = {}
    for entry in entries or []:
        key = entry_id(entry)
        if key is None:
            continue
        indexed[key] = entry
    return indexed


def utc_now_iso():
    """UTC ISO8601 timestamp, seconds precision, e.g. 2026-01-16T04:00:00Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sync(fresh, old_full, old_meta, overrides, now=None):
    """Merge fresh into the cumulative store and build the three outputs.

    Returns ``(full, delta, meta, dropped_ids)``.
    """
    now = now or utc_now_iso()

    old_map = index_by_anilist(old_full)
    fresh_map = index_by_anilist(fresh)

    # Upserts: fresh entries that are new or differ from old (whole object).
    upserts = []
    for entry in fresh or []:
        key = entry_id(entry)
        if key is None:
            continue
        old_entry = old_map.get(key)
        if old_entry is None or old_entry != entry:
            upserts.append(entry)

    # Cumulative store starts from old, never from fresh. Add-or-replace only.
    merged = dict(old_map)
    for entry in upserts:
        key = entry_id(entry)
        if key in overrides:
            # Overrides always win and must never be overwritten by Fribb data.
            continue
        merged[key] = entry

    # Overrides are applied last.
    for key, entry in overrides.items():
        merged[str(key)] = entry

    # Deletions are never applied. Report them for monitoring only.
    dropped_ids = [key for key in old_map if key not in fresh_map]
    dropped_ids.sort(key=lambda value: (0, int(value)) if value.isdigit() else (1, value))

    from_version = old_meta.get("version") if isinstance(old_meta, dict) else None

    full = list(merged.values())
    delta = {
        "from_version": from_version,
        "to_version": now,
        "upserts": upserts,
    }
    meta = {
        "version": now,
        "count": len(full),
        "generated_at": now,
    }
    return full, delta, meta, dropped_ids


def _existing_path(path):
    return path if path and Path(path).is_file() else None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Cumulative Fribb anime-mapping sync (no network I/O)."
    )
    parser.add_argument("--fresh", required=True, help="Fresh Fribb dataset (JSON array).")
    parser.add_argument("--old-full", default=None, help="Previous cumulative full.json (optional).")
    parser.add_argument("--old-meta", default=None, help="Previous meta.json (optional).")
    parser.add_argument("--overrides", required=True, help="overrides.json (object keyed by anilist_id).")
    parser.add_argument("--out-full", required=True, help="Output path for cumulative full.json.")
    parser.add_argument("--out-delta", required=True, help="Output path for delta.json.")
    parser.add_argument("--out-meta", required=True, help="Output path for meta.json.")
    args = parser.parse_args(argv)

    fresh = load_json(args.fresh)
    if not isinstance(fresh, list):
        raise SystemExit("--fresh must be a JSON array of mapping entries")

    old_full = load_json(_existing_path(args.old_full)) if _existing_path(args.old_full) else []
    old_meta = load_json(_existing_path(args.old_meta)) if _existing_path(args.old_meta) else None

    overrides = load_json(args.overrides)
    if not isinstance(overrides, dict):
        raise SystemExit("--overrides must be a JSON object keyed by anilist_id string")

    full, delta, meta, dropped_ids = sync(fresh, old_full, old_meta, overrides)

    save_json(args.out_full, full, compact=True)
    save_json(args.out_delta, delta)
    save_json(args.out_meta, meta)

    eprint(f"fribb-sync: old entries: {len(old_full)}")
    eprint(f"fribb-sync: fresh entries: {len(fresh)}")
    eprint(f"fribb-sync: upserts: {len(delta['upserts'])}")
    eprint(f"fribb-sync: full entries: {meta['count']}")
    eprint(f"fribb-sync: dropped_ids ({len(dropped_ids)}): {json.dumps(dropped_ids)}")
    eprint(
        "fribb-sync: dropped_ids are logged for monitoring only; "
        "they are NOT removed from full.json and are NOT part of delta.json"
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
