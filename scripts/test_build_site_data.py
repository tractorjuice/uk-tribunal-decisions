#!/usr/bin/env python3
"""Unit tests for the site builder.

Dependency-free, like test_extraction.py: `python3 scripts/test_build_site_data.py`.

The search index is committed to the repository, so the builder has to write
the same bytes for the same decisions. It did not: terms entered each shard in
set-iteration order, which Python randomises per process. A refresh that found
no new decisions still rewrote 652 of the 1,255 shards, identical as data and
different only in key order. Which phrases made the cut depended on the same
order whenever phrases tied at the cap.

A decision's search id is its position in the decisions array, and new
decisions arrived at the front, so every refresh moved every id and rewrote
every shard.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent

# Several words share the "se" prefix so they land in one shard, where their
# relative order is what used to vary.
LEASE_TEXT = (
    "the service charge for the second floor flat was set down in a schedule "
    "served on the leaseholder under section twenty and the tribunal settled "
    "several separate service charge disputes at one sitting"
)
RENT_TEXT = (
    "the rent officer registered a fair rent for the regulated tenancy and "
    "the landlord referred the matter to the committee"
)

# 26 of 50 documents: enough for the lease text's word pairs to be indexed as
# phrases (at least MIN_PHRASE_DOCS), and under the MAX_PHRASE_DOC_RATIO ceiling.
DECISIONS = [{"full_text": LEASE_TEXT}] * 26 + [{"full_text": RENT_TEXT}] * 24

BUILD_IN_CHILD = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import build_site_data
for name, value in json.loads(sys.argv[3]).items():
    setattr(build_site_data, name, value)
decisions = [dict(d) for d in json.load(sys.stdin)]
build_site_data.build_search_index(decisions, Path(sys.argv[2]))
"""


def build_index(hash_seed, decisions=DECISIONS, **settings):
    """Build the index in a fresh interpreter; return {file name: bytes}.

    A fresh interpreter is the point: string hashing is fixed for the life of a
    process, so two builds in one process can never disagree. `settings`
    overrides the builder's module constants, such as MAX_PHRASES.
    """
    with tempfile.TemporaryDirectory() as out_dir:
        subprocess.run(
            [sys.executable, "-c", BUILD_IN_CHILD, str(SCRIPTS), out_dir,
             json.dumps(settings)],
            input=json.dumps(decisions),
            env=dict(os.environ, PYTHONHASHSEED=str(hash_seed)),
            text=True,
            capture_output=True,
            check=True,
        )
        return {p.name: p.read_bytes() for p in Path(out_dir).glob("*.json")}


# Runs main() the way the weekly refresh does, against files in `work`. The
# site data written by one build is still there for the next.
BUILD_SITE_IN_CHILD = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import build_site_data
work = Path(sys.argv[2])
build_site_data.SEARCH_DIR = work / "search"
sys.argv = ["build_site_data.py", "--input", str(work / "input.json"),
            "--wales", str(work / "no-wales.json"),
            "--output", str(work / "decisions.json"), "--skip-pages"]
build_site_data.main()
"""


def build_site(work, decisions):
    """Build the site data for `decisions` in `work`.

    Returns the URLs in published order, whose positions are the search ids,
    and the shards as {file name: bytes}.
    """
    (work / "input.json").write_text(json.dumps({"metadata": {}, "decisions": decisions}))
    result = subprocess.run(
        [sys.executable, "-c", BUILD_SITE_IN_CHILD, str(SCRIPTS), str(work)],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"build failed:\n{result.stderr}")
    site = json.loads((work / "decisions.json").read_text())
    urls = [d["url"] for d in site["decisions"]]
    shards = {p.name: p.read_bytes() for p in (work / "search").glob("*.json")}
    return urls, shards


A = {"url": "https://example.test/a", "full_text": "service charge dispute over roof repairs"}
B = {"url": "https://example.test/b", "full_text": "rent repayment order against landlord"}
C = {"url": "https://example.test/c", "full_text": "leasehold enfranchisement premium valuation"}
# Every word starts "zy", so the new decision's terms all land in one shard.
NEW = {"url": "https://example.test/new", "full_text": "zymurgy zygote zydeco"}


class TestSearchIdsAreStable(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_existing_decisions_keep_their_ids_when_new_ones_arrive(self):
        _, before = build_site(self.work, [A, B, C])
        # The scrape puts new decisions first, and can reorder the rest.
        urls, after = build_site(self.work, [NEW, C, A, B])

        self.assertEqual(urls, [A["url"], B["url"], C["url"], NEW["url"]])
        changed = sorted(name for name in set(before) | set(after)
                         if before.get(name) != after.get(name))
        self.assertEqual(changed, ["shards.json", "zy.json"])

    def test_a_removed_decision_is_dropped(self):
        build_site(self.work, [A, B, C])
        urls, _ = build_site(self.work, [C, A])

        self.assertEqual(urls, [A["url"], C["url"]])

    def test_unreadable_previous_site_data_falls_back_to_input_order(self):
        # A corrupt decisions.json is a broken site; the refresh must still
        # be able to rebuild it rather than fail on reading it.
        (self.work / "decisions.json").write_text("<<<<<<< HEAD")
        urls, _ = build_site(self.work, [B, A])

        self.assertEqual(urls, [B["url"], A["url"]])


class TestHubPages(unittest.TestCase):
    def test_same_date_decisions_are_listed_newest_published_first(self):
        # New decisions now go on the end of the array, so array order no
        # longer puts the latest publications first among same-date ties.
        sys.path.insert(0, str(SCRIPTS))
        import build_site_data

        earlier = {"url": "earlier", "decision_date": "2026-11-10",
                   "published_at": "2026-11-12T09:00:00Z"}
        later = {"url": "later", "decision_date": "2026-11-10",
                 "published_at": "2026-11-19T09:00:00Z"}
        wales = {"url": "wales", "decision_date": "2026-11-10"}  # no published_at
        newer_date = {"url": "newer-date", "decision_date": "2026-11-11",
                      "published_at": "2026-11-12T09:00:00Z"}

        listed = build_site_data.most_recent([wales, earlier, newer_date, later])

        self.assertEqual([d["url"] for d in listed], ["newer-date", "later", "earlier", "wales"])


class TestSearchIndexIsReproducible(unittest.TestCase):
    def test_same_decisions_give_the_same_bytes_under_any_hash_seed(self):
        first = build_index(hash_seed=1)
        second = build_index(hash_seed=2)

        # Guard the fixture: the comparison means nothing unless one shard
        # holds several words and at least one phrase.
        se_terms = json.loads(first["se.json"])
        self.assertIn("section", se_terms)
        self.assertIn("several", se_terms)
        self.assertIn("service_charge", se_terms)

        self.assertEqual(sorted(first), sorted(second))
        differing = sorted(name for name in first if first[name] != second[name])
        self.assertEqual(differing, [], "shards differ between hash seeds")

    def test_phrases_tied_at_the_cap_are_chosen_the_same_way_under_any_hash_seed(self):
        # Nine adjacent pairs from one document, so all nine tie on document
        # frequency, and only four fit under the cap. most_common broke the tie
        # by insertion order, which within a document is frozenset order.
        decisions = [{"full_text": "alpha bravo charlie delta echo foxtrot golf hotel india juliet"}]
        settings = dict(MAX_PHRASES=4, MIN_PHRASE_DOCS=1, MAX_PHRASE_DOC_RATIO=1.0)

        chosen = []
        for seed in range(1, 7):
            index = build_index(seed, decisions, **settings)
            phrases = set()
            for name, data in index.items():
                if name != "shards.json":
                    phrases.update(term for term in json.loads(data) if "_" in term)
            chosen.append(phrases)

        # Guard the fixture: the cap has to bite for the tie to matter.
        self.assertEqual(len(chosen[0]), 4)
        for seed, phrases in enumerate(chosen[1:], start=2):
            self.assertEqual(phrases, chosen[0], f"hash seed {seed} chose different phrases")


if __name__ == "__main__":
    unittest.main(verbosity=2)
