#!/usr/bin/env python3
"""Unit tests for the site builder.

Dependency-free, like test_extraction.py: `python3 scripts/test_build_site_data.py`.

The search index is committed to the repository, so the builder has to write
the same bytes for the same decisions. It did not: terms entered each shard in
set-iteration order, which Python randomises per process. A refresh that found
no new decisions still rewrote 652 of the 1,255 shards, identical as data and
different only in key order. Which phrases made the cut depended on the same
order whenever phrases tied at the cap.
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
