#!/usr/bin/env python3
"""Integration tests for checks/n2_check.py: real artifacts in, three-valued exit out.

Two things are pinned here.

1. The gate PASSES on the round trip this task actually made against the host
   (`<repo>/.n2-artifacts.json` + the transcript copy recorded inside it), and it
   honours `N2_TRANSCRIPT` instead of a baked-in path.
2. The gate has teeth: each mutation of the real artifacts or of the probe corpus
   turns its named check red and exits 1, while an unreadable input exits 2 so a
   missing file is never read as a verdict.  And `--self-test`'s must-fire loop is
   itself shown to be load-bearing by an injected no-op mutation.

Run: python3 -m pytest tests/test_n2_check_integration.py
"""

import json
import os
import subprocess
import sys

import pytest

import conftest
import n2_canonical
import n2_live
import n2_transcript

n2_check = conftest.load_module("n2_check_under_test", conftest.CHECK)

NEEDS_REAL_RUN = pytest.mark.skipif(
    not conftest.real_run_available(),
    reason="no live round trip on disk: run `python3 src/n2_live.py --envelope ...` "
           "and `--capture-transcript` first")


def _run_check(*args, **kwargs):
    """Run the gate with N2_TRANSCRIPT cleared unless a test supplies it explicitly."""
    extra = dict(kwargs.pop("env", {}))
    env = dict(os.environ)
    env.pop("N2_TRANSCRIPT", None)
    env.update(extra)
    return subprocess.run([sys.executable, conftest.CHECK] + list(args),
                          capture_output=True, text=True, cwd=conftest.ROOT, env=env,
                          timeout=600)


def _failed_gate_names(stdout):
    """Gate names from the per-gate status table, in order, deduplicated.

    The same name is repeated inside the trailing "Failed checks and their evidence"
    block, so a naive scan double-counts and every "== [one gate]" assertion reads as a
    mismatch on a correct run.
    """
    known = dict((name, None) for name, _fn in n2_check.GATES)
    seen = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("FAIL "):
            name = stripped.split()[1]
            if name in known and name not in seen:
                seen.append(name)
    return seen


def _real_records():
    records, _errors = n2_transcript.load_transcript(conftest.TRANSCRIPT)
    return records


def _bundle(tmp_path, records=None, artifacts=None, corpus_mutation=None):
    if artifacts is None:
        artifacts = json.loads(open(conftest.ARTIFACTS, encoding="utf-8").read())
    if records is None:
        records = _real_records()
    corpus_path = (conftest.apply_corpus_mutation(corpus_mutation, tmp_path)
                   if corpus_mutation else conftest.CORPUS)
    return n2_check.Bundle(records, artifacts, conftest.TRANSCRIPT, corpus_path=corpus_path)


def _gate_result(bundle, gate):
    rows = n2_check.run_gates(bundle, only={gate}, skip=(n2_check.SELF_GATE,))
    assert rows, "gate %r is not in GATES" % gate
    return rows[0][1], rows[0][2]


def _rebind_capture(doc, transcript_path):
    """Point the capture block at a mutated transcript copy, for isolated controls."""
    digest = n2_transcript.file_digest(str(transcript_path))
    blocks = [doc.get("transcript_capture")] + [
        r.get("transcript_capture") for r in doc.get("runs", [])
        if "transcript_capture" in r]
    for block in blocks:
        if block:
            block["copy"] = str(transcript_path)
            block["source_sha1"] = digest
            block["source_bytes"] = os.path.getsize(str(transcript_path))
    return doc


def _real_pair(tmp_path):
    """Copy the real artifacts to tmp_path so a mutation cannot touch the originals."""
    doc = json.loads(open(conftest.ARTIFACTS, encoding="utf-8").read())
    path = tmp_path / "artifacts.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return doc, str(path)


# ------------------------------------------------------------------- the real run
@NEEDS_REAL_RUN
def test_check_passes_on_the_real_round_trip():
    proc = _run_check("--verbose")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "RESULT: ALL PASS" in proc.stdout
    assert "gates failed : 0" in proc.stdout
    assert len(_failed_gate_names(proc.stdout)) == 0


@NEEDS_REAL_RUN
def test_check_reports_its_denominator():
    proc = _run_check()
    reported = [line for line in proc.stdout.splitlines() if line.startswith("gates run")]
    assert reported, proc.stdout
    count = int(reported[0].split(":")[1])
    assert count == len(n2_check.GATES), "the check ran %d of %d gates" % (
        count, len(n2_check.GATES))


@NEEDS_REAL_RUN
def test_check_honours_the_transcript_env_var():
    proc = _run_check(env={"N2_TRANSCRIPT": conftest.TRANSCRIPT})
    assert proc.returncode == 0, proc.stdout
    assert conftest.TRANSCRIPT in proc.stdout


@NEEDS_REAL_RUN
def test_check_ignores_no_transcript_path_when_env_is_set(tmp_path):
    """If the env var were ignored the default file would be read and this would pass."""
    decoy = tmp_path / "decoy.jsonl"
    decoy.write_text(json.dumps({"type": "user", "sessionId": "x",
                                 "message": {"content": []}}) + "\n", encoding="utf-8")
    proc = _run_check(env={"N2_TRANSCRIPT": str(decoy)})
    assert proc.returncode == 1, proc.stdout
    assert "live_call_present_in_transcript" in _failed_gate_names(proc.stdout)


def test_check_missing_transcript_is_a_precondition(tmp_path):
    proc = _run_check("--transcript", str(tmp_path / "absent.jsonl"))
    assert proc.returncode == 2, proc.stdout
    assert "PRECONDITION" in proc.stdout
    assert "RESULT: PRECONDITION UNMET" in proc.stdout
    assert "RESULT: FAILURES PRESENT" not in proc.stdout


def test_check_missing_artifacts_is_a_precondition(tmp_path):
    if not os.path.exists(conftest.TRANSCRIPT):
        pytest.skip("no transcript copy to pair with")
    proc = _run_check("--transcript", conftest.TRANSCRIPT,
                      "--artifacts", str(tmp_path / "no-artifacts.json"))
    assert proc.returncode == 2, proc.stdout
    assert "PRECONDITION" in proc.stdout


def test_check_unparseable_transcript_is_a_precondition(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("this is not json\n\nalso not json\n", encoding="utf-8")
    proc = _run_check("--transcript", str(bad), "--artifacts", conftest.ARTIFACTS)
    assert proc.returncode == 2, proc.stdout
    assert "PRECONDITION" in proc.stdout


# ---------------------------------------------------------------- the mutation set
@NEEDS_REAL_RUN
def test_aliased_nested_name_turns_its_gate_red(tmp_path):
    doc, path = _real_pair(tmp_path)
    doc["runs"][-1]["nested"]["envelope"]["tool_call"]["name"] = "bash"
    (tmp_path / "artifacts.json").write_text(json.dumps(doc), encoding="utf-8")
    proc = _run_check("--transcript", conftest.TRANSCRIPT, "--artifacts", path)
    assert proc.returncode == 1, proc.stdout
    assert _failed_gate_names(proc.stdout) == ["nested_wrapper_uses_canonical_name"], \
        proc.stdout


@NEEDS_REAL_RUN
def test_wrapper_nested_name_turns_its_gate_red(tmp_path):
    doc, path = _real_pair(tmp_path)
    call = doc["runs"][-1]["nested"]["envelope"]["tool_call"]
    call["name"] = "mcp_call"
    call["input"] = {"toolName": "Bash", "arguments": {"command": "uname -s"}}
    (tmp_path / "artifacts.json").write_text(json.dumps(doc), encoding="utf-8")
    proc = _run_check("--transcript", conftest.TRANSCRIPT, "--artifacts", path)
    assert proc.returncode == 1, proc.stdout
    assert "nested_wrapper_uses_canonical_name" in _failed_gate_names(proc.stdout)
    assert "E_WRAPPER_TARGET_NOT_CANONICAL" in proc.stdout


@NEEDS_REAL_RUN
def test_nested_result_claimed_from_the_same_process_turns_its_gate_red(tmp_path):
    doc, path = _real_pair(tmp_path)
    run = doc["runs"][-1]
    run["nested"]["dispatch"]["observed"]["pid"] = run["top_level"]["harness_pid"]
    (tmp_path / "artifacts.json").write_text(json.dumps(doc), encoding="utf-8")
    proc = _run_check("--transcript", conftest.TRANSCRIPT, "--artifacts", path)
    assert proc.returncode == 1, proc.stdout
    assert _failed_gate_names(proc.stdout) == ["nested_result_from_subprocess"], proc.stdout


@NEEDS_REAL_RUN
def test_undispatched_nested_call_turns_its_gate_red(tmp_path):
    doc, path = _real_pair(tmp_path)
    doc["runs"][-1]["nested"]["dispatch"] = {"attempted": False, "reason": "skipped"}
    (tmp_path / "artifacts.json").write_text(json.dumps(doc), encoding="utf-8")
    proc = _run_check("--transcript", conftest.TRANSCRIPT, "--artifacts", path)
    assert proc.returncode == 1
    assert "nested_result_from_subprocess" in _failed_gate_names(proc.stdout)


@NEEDS_REAL_RUN
def test_echoed_child_secret_turns_its_gate_red(tmp_path):
    """Replay the nested stdout at the top level and the pipe-capture claim collapses.

    The capture gate is re-pointed at the mutated copy (its own subject is binding, not
    echo hygiene), so this control isolates one gate instead of firing two.
    """
    doc, path = _real_pair(tmp_path)
    run = doc["runs"][-1]
    secret = run["nested"]["dispatch"]["observed"]["secret"]
    records, _errors = n2_transcript.load_transcript(conftest.TRANSCRIPT)
    mutated = tmp_path / "echoed.jsonl"
    with open(mutated, "w", encoding="utf-8") as handle:
        for rec in records:
            text = json.dumps(rec, ensure_ascii=False)
            if n2_live.TOP_MARK % run["roundtrip_nonce"] in text:
                text = text.replace(n2_live.TOP_MARK % run["roundtrip_nonce"],
                                    n2_live.TOP_MARK % run["roundtrip_nonce"] + " " + secret)
            handle.write(text + "\n")
    _rebind_capture(doc, mutated)
    (tmp_path / "artifacts.json").write_text(json.dumps(doc), encoding="utf-8")
    proc = _run_check("--transcript", str(mutated), "--artifacts", path)
    assert proc.returncode == 1, proc.stdout
    assert _failed_gate_names(proc.stdout) == ["nested_child_result_not_echoed_at_top_level"], \
        proc.stdout


@NEEDS_REAL_RUN
def test_transcript_replaced_after_capture_breaks_the_binding_gate(tmp_path):
    doc, path = _real_pair(tmp_path)
    aliased = tmp_path / "aliased.jsonl"
    text = open(conftest.TRANSCRIPT, encoding="utf-8").read()
    aliased.write_text(text.replace('"name": "Bash"', '"name": "bash"'), encoding="utf-8")
    proc = _run_check("--transcript", str(aliased), "--artifacts", path)
    assert proc.returncode == 1, proc.stdout
    assert "artifacts_bound_to_captured_transcript" in _failed_gate_names(proc.stdout)


# --------------------------------------------------- corpus controls, in-process
CORPUS_CONTROLS = {
    "drop_a_required_shape": "probe_corpus_covers_each_shape",
    "single_verdict_corpus": "probe_corpus_covers_each_shape",
    "unreasoned_case": "probe_expectations_are_literal_not_hardcoded_pass",
    "duplicate_ids": "probe_expectations_are_literal_not_hardcoded_pass",
    "rubber_stamp_pass": "probe_verdicts_match_validator",
    "relabel_alias_as_direct": "direct_passes_and_other_shapes_fail",
}


@pytest.mark.parametrize("mutation,gate", sorted(CORPUS_CONTROLS.items()))
@NEEDS_REAL_RUN
def test_corpus_mutations_fire_their_gates(mutation, gate, tmp_path):
    records, _errors = n2_transcript.load_transcript(conftest.TRANSCRIPT)
    artifacts = json.loads(open(conftest.ARTIFACTS, encoding="utf-8").read())
    path = conftest.apply_corpus_mutation(mutation, tmp_path)
    bundle = n2_check.Bundle(records, artifacts, conftest.TRANSCRIPT, corpus_path=path)
    rows = dict((name, (ok, evidence)) for name, ok, evidence in n2_check.run_gates(
        bundle, skip=(n2_check.SELF_GATE,)))
    ok, evidence = rows[gate]
    assert ok is False, "corpus mutation %r left gate %r green: %s" % (mutation, gate, evidence)


@NEEDS_REAL_RUN
def test_baseline_artifacts_keep_every_corpus_gate_green(tmp_path):
    """The paired compliant side: the same gates read the unmutated corpus as pass."""
    records, _errors = n2_transcript.load_transcript(conftest.TRANSCRIPT)
    artifacts = json.loads(open(conftest.ARTIFACTS, encoding="utf-8").read())
    bundle = n2_check.Bundle(records, artifacts, conftest.TRANSCRIPT)
    rows = dict((name, ok) for name, ok, _ in n2_check.run_gates(
        bundle, skip=(n2_check.SELF_GATE,)))
    for gate in set(CORPUS_CONTROLS.values()):
        assert rows[gate] is True, "%s is red before any mutation" % gate


def _without_runs(doc):
    doc["runs"] = []
    return doc


def _envelope_loses_its_invocation_id(doc):
    del doc["runs"][-1]["nested"]["envelope"]["tool_call"]["invocation_id"]
    return doc


def _nested_envelope_absent(doc):
    doc["runs"][-1]["nested"].pop("envelope")
    return doc


ARTIFACT_CONTROLS = {
    "no_run_recorded": (_without_runs, "live_roundtrip_run_recorded"),
    "envelope_loses_its_invocation_id": (_envelope_loses_its_invocation_id,
                                         "nested_block_inside_the_same_live_call"),
    "nested_envelope_absent": (_nested_envelope_absent, "nested_wrapper_uses_canonical_name"),
}


@pytest.mark.parametrize("name", sorted(ARTIFACT_CONTROLS))
@NEEDS_REAL_RUN
def test_artifact_mutations_fire_their_gates(name, tmp_path):
    mutate, gate = ARTIFACT_CONTROLS[name]
    doc = json.loads(open(conftest.ARTIFACTS, encoding="utf-8").read())
    ok, evidence = _gate_result(_bundle(tmp_path, artifacts=mutate(doc)), gate)
    assert ok is False, "artifact mutation %r left gate %r green: %s" % (name, gate, evidence)


@NEEDS_REAL_RUN
def test_registry_mutation_fires_the_declaration_gate(monkeypatch):
    """A canonical entry with no recorded discovery source must not read as declared."""
    monkeypatch.setitem(n2_canonical.PROVENANCE, "canonical_live_entry", {})
    ok, evidence = _gate_result(_bundle(None), "canonical_entry_declared_from_host_docs")
    assert ok is False, "erasing PROVENANCE left the declaration gate green: %s" % evidence


@NEEDS_REAL_RUN
def test_registry_mutation_fires_the_confirmation_gate(monkeypatch):
    """If the canonical entry is renamed, no transcript can confirm it as live."""
    monkeypatch.setattr(n2_canonical, "CANONICAL_LIVE_ENTRY", "Bazz")
    ok, evidence = _gate_result(_bundle(None), "canonical_entry_confirmed_by_real_call")
    assert ok is False, "a made-up entry read as confirmed: %s" % evidence


@NEEDS_REAL_RUN
def test_every_gate_has_a_control_and_not_just_the_self_test():
    """Denominator: --self-test mutations plus this file's controls cover every gate."""
    owned = set(gate for gate, _label, _fn in n2_check.MUTATIONS.values())
    owned |= set(CORPUS_CONTROLS.values())
    owned |= set(gate for _name, (_mutate, gate) in ARTIFACT_CONTROLS.items())
    owned |= {"canonical_entry_declared_from_host_docs",
              "canonical_entry_confirmed_by_real_call",
              "live_call_present_in_transcript",
              "nested_child_result_not_echoed_at_top_level",
              "artifacts_bound_to_captured_transcript"}
    unowned = [name for name, _fn in n2_check.GATES
               if name != n2_check.SELF_GATE and name not in owned]
    assert not unowned, "these gates can never be shown red: %s" % unowned


def test_no_gate_name_is_duplicated_or_missing():
    names = [name for name, _fn in n2_check.GATES]
    assert len(names) == len(set(names)), "duplicate gate names"
    assert len(names) >= 12, names


# ------------------------------------------------------------- the self-test itself
def test_self_test_exits_zero():
    proc = _run_check("--self-test")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "every gate has a mutation that turns it red" in proc.stdout


def test_self_test_mutates_at_least_one_control_per_gate():
    proc = _run_check("--self-test")
    fired = [line for line in proc.stdout.splitlines() if "FIRED" in line]
    assert len(fired) >= len(n2_check.MUTATIONS), "some mutation did not report FIRED"
    assert "mutations applied : %d" % len(n2_check.MUTATIONS) in proc.stdout


def test_self_test_sheds_a_no_op_mutation(monkeypatch):
    """If a mutation stopped mattering, --self-test must go red -- not stay green."""
    def noop(records, artifacts, tmp):
        return records, artifacts, {}

    gate = "live_call_present_in_transcript"
    monkeypatch.setitem(n2_check.MUTATIONS, "inert_control", (gate, "does nothing", noop))
    assert n2_check.self_test() == 1


def test_harness_never_bakes_in_a_personal_transcript_path():
    source = open(conftest.LIVE, encoding="utf-8").read()
    for needle in ("/Users/", "/root/", ".qoder-cn/projects"):
        assert needle not in source, "the harness hardcodes %r" % needle
    assert n2_live.DEFAULT_TRANSCRIPT_COPY.startswith(conftest.ROOT)


def test_check_never_bakes_in_a_personal_transcript_path():
    """The default is inside the repo; nothing here points at a session directory."""
    source = open(conftest.CHECK, encoding="utf-8").read()
    for needle in ("/Users/", "/root/", ".qoder-cn/projects"):
        assert needle not in source, "the check hardcodes %r" % needle
    assert "N2_TRANSCRIPT" in source
    assert conftest.ROOT in source or "ROOT" in source
