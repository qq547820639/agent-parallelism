"""Path setup so the tests can import the plain-stdlib modules under test.

The n2 code is a set of scripts (`python3 src/n2_live.py`), not an installable
package, so the tests import it the way `checks/n2_check.py` does: by putting the
directories on sys.path.

TRANSCRIPT / ARTIFACTS are the paths the live round trip actually wrote, because
test_n2_check_integration.py reads those real artifacts rather than re-deriving them
from the unit-test fixtures.  `N2_TRANSCRIPT` overrides the transcript, exactly as it
does for the check, so an exported run can be replayed against its own copy.
"""

import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
SCRIPTS = os.path.join(ROOT, "scripts")
CHECKS = os.path.join(ROOT, "checks")

for _path in (SRC, SCRIPTS, ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

TRANSCRIPT = os.environ.get("N2_TRANSCRIPT") or os.path.join(ROOT, ".n2-transcript.jsonl")
ARTIFACTS = os.path.join(ROOT, ".n2-artifacts.json")
CHECK = os.path.join(CHECKS, "n2_check.py")
CORPUS = os.path.join(SCRIPTS, "malformed_probe_cases.py")
LIVE = os.path.join(SRC, "n2_live.py")
NESTED_CHILD = os.path.join(SRC, "n2_nested_child.py")


def load_module(name, path):
    """Import a script by path (used for checks/ and scripts/ files)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def real_run_available():
    """True when the live round trip has run and its transcript has been captured."""
    return os.path.exists(TRANSCRIPT) and os.path.exists(ARTIFACTS)


# ---------------------------------------------------------------------------
# Corpus mutations, shared by the corpus self-test test and the gate's controls.
# Each one is a text patch that FAILS if its pattern is not found, so a stale
# pattern can never pass the mutation off as applied (rc would then stay 0 and the
# control would read as "fired").
# ---------------------------------------------------------------------------
CORPUS_MUTATIONS = {
    # name: (old substring, new substring, minimum expected occurrences)
    "drop_a_required_shape": ('"shape": "no_envelope"', '"shape": "fabricated"', 4),
    "single_verdict_corpus": ('"expected_verdict": "pass"',
                              '"expected_verdict": "fail"', 3),
    "unreasoned_case": ('"expected_reason": "E_FABRICATED_NAME",',
                        '"expected_reason": "",', 1),
    "duplicate_ids": ('"id": "n2p_alias_upper",', '"id": "n2p_alias_lowercase",', 1),
    "rubber_stamp_pass": ('"expected_verdict": "fail"', '"expected_verdict": "pass"', 27),
    "relabel_alias_as_direct": ('"shape": "aliased"', '"shape": "direct"', 4),
}


def apply_corpus_mutation(name, dest_dir):
    """Write a mutated copy of the probe corpus into dest_dir; return its path."""
    old, new, minimum = CORPUS_MUTATIONS[name]
    source = open(CORPUS, "r", encoding="utf-8").read()
    hits = source.count(old)
    if hits < minimum:
        raise AssertionError("corpus mutation %r would be a no-op: pattern found %d time(s), "
                             "expected >= %d" % (name, hits, minimum))
    mutated = source.replace(old, new)
    if mutated == source:
        raise AssertionError("corpus mutation %r changed nothing" % name)
    path = os.path.join(str(dest_dir), "mutated_%s.py" % name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(mutated)
    return path


@pytest.fixture
def install_corpus_mutation(tmp_path):
    """Pytest fixture form of apply_corpus_mutation, scoped to the test's tmp_path."""
    def _install(name):
        return apply_corpus_mutation(name, tmp_path)
    return _install
