#!/usr/bin/env python3
"""
Store and restore data/tribunal_decisions_full.json as a GitHub Release asset.

The enriched corpus is about 385 MB and every refresh rewrites it. Git LFS keeps
each version whole, which exhausted the repository's LFS budget and made most
weekly refreshes fail at the final push. The site never serves this file, so it
does not need to be in Git: it only has to survive between pipeline runs.

Each push uploads under a new dated name and removes older copies only once the
new one has landed. `gh release upload --clobber` deletes the old asset before
uploading, so a failed upload would lose the only copy.

Requires an authenticated GitHub CLI (gh), or GH_TOKEN in CI.

Usage:
    # Fetch the latest copy into data/
    python3 full_dataset_store.py pull [--output FILE] [--force]

    # Upload the local copy (refuses to replace a larger dataset)
    python3 full_dataset_store.py push [--input FILE] [--allow-shrink]
"""

import argparse
import gzip
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from build_site_data import ENRICHED_INPUT, ROOT, looks_like_lfs_pointer

RELEASE_TAG = "full-dataset"

# The timestamp comes first and is fixed-width, so names sort chronologically.
ASSET_NAME = re.compile(r"^tribunal_decisions_full-(\d{8}T\d{6}Z)-n(\d+)\.json\.gz$")

# The new copy plus the one before it, so a bad refresh can be rolled back.
KEEP_VERSIONS = 2


def gh(*args: str) -> str:
    """Run the GitHub CLI and return stdout, stopping with its error on failure."""
    try:
        result = subprocess.run(["gh", *args], cwd=ROOT, capture_output=True, text=True)
    except FileNotFoundError:
        raise SystemExit("The GitHub CLI (gh) is required: https://cli.github.com/")
    if result.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout


def stored_assets() -> list[dict]:
    """Dataset assets on the release, oldest first."""
    raw = gh("release", "view", RELEASE_TAG, "--json", "assets")
    assets = [a for a in json.loads(raw)["assets"] if ASSET_NAME.match(a["name"])]
    return sorted(assets, key=lambda a: a["name"])


def stored_record_count(asset: dict) -> int:
    return int(ASSET_NAME.match(asset["name"]).group(2))


def count_records(path: Path) -> int:
    """Record count of a dataset file; stops if it is missing or not a dataset."""
    try:
        with open(path, encoding="utf-8") as f:
            return len(json.load(f)["decisions"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"{path} is not a usable dataset: {exc!r}")


def pull(output: Path, force: bool) -> None:
    if output.exists() and not looks_like_lfs_pointer(output) and not force:
        raise SystemExit(
            f"{output} already exists and may hold work that has not been pushed. "
            "Use --force to replace it."
        )

    assets = stored_assets()
    if not assets:
        raise SystemExit(f"Release {RELEASE_TAG} has no dataset asset.")
    latest = assets[-1]
    name = latest["name"]

    partial = output.with_name(output.name + ".part")
    with tempfile.TemporaryDirectory() as tmp:
        print(f"Downloading {name} ...")
        gh("release", "download", RELEASE_TAG, "--pattern", name, "--dir", tmp)
        # gzip checks its CRC on reaching the end of the stream, so a truncated
        # or corrupt download raises here rather than producing a bad dataset.
        with gzip.open(Path(tmp) / name, "rb") as src, open(partial, "wb") as dst:
            shutil.copyfileobj(src, dst)

    count = count_records(partial)
    expected = stored_record_count(latest)
    if count != expected:
        partial.unlink()
        raise SystemExit(f"{name} holds {count:,} records, expected {expected:,}.")

    os.replace(partial, output)
    print(f"Restored {count:,} records to {output}")


def push(input_path: Path, allow_shrink: bool) -> None:
    count = count_records(input_path)

    assets = stored_assets()
    if assets:
        stored = stored_record_count(assets[-1])
        if count < stored and not allow_shrink:
            raise SystemExit(
                f"{input_path} has {count:,} records but the stored copy has "
                f"{stored:,}. Refusing to shrink the dataset without --allow-shrink."
            )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"tribunal_decisions_full-{stamp}-n{count}.json.gz"

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / name
        print(f"Compressing {count:,} records ...")
        with open(input_path, "rb") as src, gzip.open(archive, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst)
        size = archive.stat().st_size
        print(f"Uploading {name} ({size / 1e6:.0f} MB) ...")
        gh("release", "upload", RELEASE_TAG, str(archive))

    assets = stored_assets()
    uploaded = next((a for a in assets if a["name"] == name), None)
    if uploaded is None or uploaded["size"] != size:
        raise SystemExit(f"{name} did not land intact; older copies were left in place.")

    for old in assets[:-KEEP_VERSIONS]:
        print(f"Removing old copy {old['name']}")
        gh("release", "delete-asset", RELEASE_TAG, old["name"], "--yes")

    print(f"Stored {count:,} records as {name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Store and restore the enriched dataset as a GitHub Release asset."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    pull_parser = commands.add_parser("pull", help="Fetch the latest stored copy")
    pull_parser.add_argument("--output", type=Path, default=ENRICHED_INPUT,
                             help="Where to write it (default: data/tribunal_decisions_full.json)")
    pull_parser.add_argument("--force", action="store_true",
                             help="Replace an existing local dataset")

    push_parser = commands.add_parser("push", help="Upload the local copy")
    push_parser.add_argument("--input", type=Path, default=ENRICHED_INPUT,
                             help="File to upload (default: data/tribunal_decisions_full.json)")
    push_parser.add_argument("--allow-shrink", action="store_true",
                             help="Allow replacing a dataset that has more records")

    args = parser.parse_args()
    if args.command == "pull":
        pull(args.output, args.force)
    else:
        push(args.input, args.allow_shrink)


if __name__ == "__main__":
    main()
