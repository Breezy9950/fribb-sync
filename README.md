# fribb-sync

A standalone data pipeline that mirrors the upstream
[Fribb anime-lists](https://github.com/Fribb/anime-lists) dataset into a
**cumulative, monotonic** store and publishes a daily delta describing what
changed. It contains no application code and serves no application directly;
its only job is to consume the upstream dataset, maintain a stable cumulative
copy, and publish the change set.

The pipeline runs on GitHub Actions. Upstream data is fetched **only inside the
workflow runner**, never by this repository's local tooling.

## Published files

All three files are published as assets on the rolling release tagged `data`
and are overwritten in place each run. Stable download URLs:

| File | Contents | Stable URL |
| --- | --- | --- |
| `full.json` | Cumulative JSON array of mapping entries (Fribb schema, keyed by `anilist_id`). Contains every entry ever observed, plus overrides applied. | `https://github.com/Breezy9950/fribb-sync/releases/latest/download/full.json` |
| `delta.json` | `{ "from_version": str \| null, "to_version": str, "upserts": [entry, ...] }` | `https://github.com/Breezy9950/fribb-sync/releases/latest/download/delta.json` |
| `meta.json` | `{ "version": str, "count": int, "generated_at": str }` | `https://github.com/Breezy9950/fribb-sync/releases/latest/download/meta.json` |

- `version` / `to_version` / `generated_at` are UTC ISO8601 timestamps with
  seconds precision, e.g. `2026-01-16T04:00:00Z`.
- `from_version` is the previous run's `meta.json` `version`, or `null` on the
  first run.
- `count` is the number of entries in `full.json` for that run.

## Merge rules

Entries are keyed by `anilist_id`. Entries whose `anilist_id` is `null` or
missing are skipped.

On each run:

1. Read the fresh upstream dataset (downloaded by the workflow).
2. Read the previous `full.json` and `meta.json` if present (empty / `null` on
   the first run).
3. Load `data/overrides.json` (a dict keyed by `anilist_id` string; may be
   empty).
4. Build the merged store starting from the **old** cumulative store — never
   from fresh.
5. Compute `upserts` by comparing fresh against old:
   - `anilist_id` not in old → new, include in `upserts`
   - old entry differs from the fresh entry → changed, include in `upserts`
   - identical → skip

   Comparison is whole-object: any field difference counts as a change.
6. Apply upserts to the merged store as add-or-replace. If an id exists in
   `overrides`, the override wins and is never overwritten by Fribb data.
7. Apply overrides last: for each id in `overrides`, set `merged[id]` to the
   override entry.
8. Compute `dropped_ids` = ids present in old but absent from fresh. These are
   **logged only** (stderr and the workflow step summary). They are never
   written to `delta.json` and never remove anything from `full.json`.
9. Write `full.json` (the cumulative array), `delta.json`, and `meta.json`.

Upserts are therefore **add-or-replace only**; deletions are never applied.

### Cumulative and monotonic

`full.json` is cumulative and monotonic: it grows and updates, and its entry
count **never decreases**. Because Fribb performs full regeneration and has
historically dropped IDs (for example a mass IMDb ID removal), an entry that
disappears upstream is retained in `full.json` indefinitely. The only way an id
leaves the store is if it is removed from `data/overrides.json` and was never
part of an earlier cumulative snapshot — upstream changes alone cannot shrink
the store.

`dropped_ids` is a monitoring signal only. It is emitted to the workflow step
summary so regressions in upstream coverage are visible, but it is **not** part
of the published data contract.

## Overrides

`data/overrides.json` pins entries so upstream regeneration cannot regress
them. It is a JSON object keyed by `anilist_id` **as a string**; each value is a
full entry that replaces whatever Fribb provides for that id. Overrides are
applied last, after upserts.

Worked example:

```json
{
  "182255": {
    "anilist_id": 182255,
    "themoviedb_id": { "tv": 209867 },
    "season": { "tmdb": 1 },
    "episode_offset": { "tmdb": 28 }
  }
}
```

With this file committed, id `182255` always appears in `full.json` with
`themoviedb_id.tv = 209867`, `season.tmdb = 1`, and `episode_offset.tmdb = 28`,
regardless of what the upstream dataset contains. An override id that is absent
from upstream is still injected into `full.json`.

## Local testing

`scripts/sync.py` performs **no network I/O** — all fetching of upstream data is
done by the workflow. This is intentional and should be preserved: it is what
allows the merge logic to be tested locally with small synthetic fixtures and
zero network calls.

Run the fixture-based test harness (uses only temp files, never the real
dataset):

```bash
python3 scripts/test_sync.py
```

The harness covers: first run, no-change run, additions, field changes,
deletion safety (dropped entries retained and logged), override injection,
override precedence, and monotonicity across a sequence of runs.

## Repository layout

```
.github/workflows/fribb-sync.yml   # scheduled + manual pipeline
scripts/sync.py                    # stdlib-only merge logic (no network I/O)
scripts/test_sync.py               # local fixture test harness
data/overrides.json                # committed pin overrides (starts as {})
```
