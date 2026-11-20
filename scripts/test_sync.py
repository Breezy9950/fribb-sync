#!/usr/bin/env python3
"""Local test harness for scripts/sync.py.

Uses ONLY tiny synthetic fixtures (anilist_id 1, 2, 3, ...) written to a temp
directory. It performs no network I/O, never reads the real Fribb dataset, and
never touches the repository working directory.

Run with: python3 scripts/test_sync.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync.py"


def make_entry(aid, **fields):
    """Build a small synthetic Fribb-shaped entry."""
    entry = {
        "anilist_id": aid,
        "type": "TV",
        "themoviedb_id": {"tv": 1000 + aid},
        "season": {"tmdb": 1, "tvdb": 1},
    }
    entry.update(fields)
    return entry


def run_sync(tmp_dir, fresh, old_full=None, old_meta=None, overrides=None):
    """Run sync.py against synthetic fixtures inside tmp_dir and load outputs."""
    tmp = Path(tmp_dir)
    tmp.mkdir(parents=True, exist_ok=True)

    fresh_path = tmp / "fresh.json"
    overrides_path = tmp / "overrides.json"
    old_full_path = tmp / "old_full.json"
    old_meta_path = tmp / "old_meta.json"
    out_full = tmp / "out_full.json"
    out_delta = tmp / "out_delta.json"
    out_meta = tmp / "out_meta.json"

    fresh_path.write_text(json.dumps(fresh), encoding="utf-8")
    overrides_path.write_text(json.dumps(overrides or {}), encoding="utf-8")
    if old_full is not None:
        old_full_path.write_text(json.dumps(old_full), encoding="utf-8")
    if old_meta is not None:
        old_meta_path.write_text(json.dumps(old_meta), encoding="utf-8")

    cmd = [
        sys.executable,
        str(SYNC_SCRIPT),
        "--fresh", str(fresh_path),
        "--overrides", str(overrides_path),
        "--out-full", str(out_full),
        "--out-delta", str(out_delta),
        "--out-meta", str(out_meta),
    ]
    if old_full is not None:
        cmd += ["--old-full", str(old_full_path)]
    if old_meta is not None:
        cmd += ["--old-meta", str(old_meta_path)]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"sync.py failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"

    full = json.loads(out_full.read_text(encoding="utf-8"))
    delta = json.loads(out_delta.read_text(encoding="utf-8"))
    meta = json.loads(out_meta.read_text(encoding="utf-8"))
    return full, delta, meta, proc.stderr


def ids(entries):
    return [entry["anilist_id"] for entry in entries]


def by_id(entries):
    return {entry["anilist_id"]: entry for entry in entries}


# --- Tests -------------------------------------------------------------------


def test_first_run():
    """1. First run: no old-full, no old-meta -> from_version null, all upserts."""
    with tempfile.TemporaryDirectory() as tmp:
        fresh = [make_entry(1), make_entry(2), make_entry(3)]
        # Null anilist_id entries must be skipped entirely.
        fresh_with_null = fresh + [{"anilist_id": None, "type": "OVA"}]

        full, delta, meta, _ = run_sync(tmp, fresh_with_null)

        assert delta["from_version"] is None, delta
        assert ids(delta["upserts"]) == [1, 2, 3], delta["upserts"]
        assert len(delta["upserts"]) == 3
        assert full == fresh, full
        assert meta["count"] == 3, meta
        assert meta["version"] and meta["generated_at"]


def test_no_change():
    """2. No-change run: same fresh twice -> empty upserts, count unchanged."""
    with tempfile.TemporaryDirectory() as tmp:
        fresh = [make_entry(1), make_entry(2)]
        full1, delta1, meta1, _ = run_sync(tmp, fresh)
        full2, delta2, meta2, _ = run_sync(tmp, fresh, old_full=full1, old_meta=meta1)

        assert delta2["upserts"] == [], delta2
        assert delta2["from_version"] == meta1["version"], delta2
        assert len(full2) == len(full1) == 2, full2
        assert meta2["count"] == 2, meta2


def test_addition():
    """3. Addition: a new entry appears in upserts and in full.json."""
    with tempfile.TemporaryDirectory() as tmp:
        fresh = [make_entry(1), make_entry(2)]
        full1, _, meta1, _ = run_sync(tmp, fresh)

        fresh2 = fresh + [make_entry(3)]
        full2, delta2, _, _ = run_sync(tmp, fresh2, old_full=full1, old_meta=meta1)

        assert ids(delta2["upserts"]) == [3], delta2["upserts"]
        assert set(ids(full2)) == {1, 2, 3}, full2


def test_change():
    """4. Change: a modified field puts the entry in upserts; new value wins."""
    with tempfile.TemporaryDirectory() as tmp:
        fresh = [make_entry(1), make_entry(2)]
        full1, _, meta1, _ = run_sync(tmp, fresh)

        changed = make_entry(1, themoviedb_id={"tv": 9999}, season={"tmdb": 2, "tvdb": 2})
        fresh2 = [changed, make_entry(2)]
        full2, delta2, _, _ = run_sync(tmp, fresh2, old_full=full1, old_meta=meta1)

        assert ids(delta2["upserts"]) == [1], delta2["upserts"]
        assert by_id(full2)[1]["themoviedb_id"] == {"tv": 9999}, full2
        assert by_id(full2)[1]["season"] == {"tmdb": 2, "tvdb": 2}, full2


def test_deletion_safety():
    """5. Deletion safety: a removed entry stays in full, not in upserts, logged."""
    with tempfile.TemporaryDirectory() as tmp:
        fresh = [make_entry(1), make_entry(2)]
        full1, _, meta1, _ = run_sync(tmp, fresh)

        fresh2 = [make_entry(2)]  # anilist_id 1 dropped upstream
        full2, delta2, _, stderr = run_sync(tmp, fresh2, old_full=full1, old_meta=meta1)

        assert 1 in by_id(full2), "deleted entry must remain in full.json"
        assert delta2["upserts"] == [], delta2
        assert "dropped_ids" in stderr, stderr
        assert '"1"' in stderr, stderr


def test_override_injection():
    """6. Override injection: an override id absent from fresh appears in full."""
    with tempfile.TemporaryDirectory() as tmp:
        fresh = [make_entry(1), make_entry(2)]
        full1, _, meta1, _ = run_sync(tmp, fresh)

        overrides = {"999": make_entry(999)}
        full2, _, _, _ = run_sync(tmp, fresh, old_full=full1, old_meta=meta1, overrides=overrides)

        assert 999 in by_id(full2), full2


def test_override_precedence():
    """7. Override precedence: override values win over fresh values."""
    with tempfile.TemporaryDirectory() as tmp:
        fresh = [make_entry(1), make_entry(2)]
        full1, _, meta1, _ = run_sync(tmp, fresh)

        overrides = {"1": make_entry(1, themoviedb_id={"tv": 777}, season={"tmdb": 5})}
        full2, _, _, _ = run_sync(tmp, fresh, old_full=full1, old_meta=meta1, overrides=overrides)

        entry = by_id(full2)[1]
        assert entry["themoviedb_id"] == {"tv": 777}, entry
        assert entry["season"] == {"tmdb": 5}, entry


def test_monotonicity():
    """8. Monotonicity: change + deletion + addition never shrinks full.json."""
    with tempfile.TemporaryDirectory() as tmp:
        fresh_a = [make_entry(1), make_entry(2)]
        full_a, _, meta_a, _ = run_sync(tmp, fresh_a)
        count_a = len(full_a)

        # Change entry 1, delete entry 2, add entry 3.
        fresh_b = [make_entry(1, themoviedb_id={"tv": 5555}), make_entry(3)]
        full_b, delta_b, meta_b, stderr_b = run_sync(
            tmp, fresh_b, old_full=full_a, old_meta=meta_a
        )
        count_b = len(full_b)
        assert count_b >= count_a, (count_a, count_b)
        assert 2 in by_id(full_b), "deleted entry 2 must remain"
        assert ids(delta_b["upserts"]) == [1, 3], delta_b["upserts"]
        assert "dropped_ids" in stderr_b and '"2"' in stderr_b, stderr_b

        # Delete entry 3 as well; count must still not decrease.
        fresh_c = [make_entry(1)]
        full_c, _, meta_c, stderr_c = run_sync(
            tmp, fresh_c, old_full=full_b, old_meta=meta_b
        )
        count_c = len(full_c)
        assert count_c >= count_b, (count_b, count_c)
        assert {1, 2, 3} <= set(ids(full_c)), full_c
        assert "dropped_ids" in stderr_c and '"3"' in stderr_c, stderr_c

        assert count_a <= count_b <= count_c, (count_a, count_b, count_c)


TESTS = [
    test_first_run,
    test_no_change,
    test_addition,
    test_change,
    test_deletion_safety,
    test_override_injection,
    test_override_precedence,
    test_monotonicity,
]


def main():
    failures = 0
    for test in TESTS:
        try:
            test()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - report any harness error
            failures += 1
            print(f"ERROR {test.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"PASS {test.__name__}")

    print(f"\n{len(TESTS) - failures}/{len(TESTS)} tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
