#!/usr/bin/env python3
"""Live round trip: the harness must really execute, really fork, and really refuse.

Every test here runs `src/n2_live.py` as a subprocess (the way the host ran it) and
reads the artifacts JSON it wrote.  Two tests read the REAL artifacts of the round
trip this task made against the host, so the fixture cannot drift from the producer:
`test_real_roundtrip_artifact_is_self_consistent` and
`test_real_roundtrip_appears_in_the_captured_transcript`.

Run: python3 -m pytest tests/test_n2_live_roundtrip.py
"""

import json
import os
import subprocess
import sys

import pytest

import conftest
import n2_canonical
import n2_live
import n2_resolve
import n2_transcript

NESTED = "python3 %s" % conftest.NESTED_CHILD


def _envelope_file(tmp_path, payload, name="nested_tool_call.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _payload(name="Bash", command=NESTED, child="default", invocation_id="roundtrip-test-1"):
    call = {"name": name, "input": {"command": command}}
    if child == "default":
        call["child"] = {"agent": "n2-bench-child", "depth": 1}
    elif child is not None:
        call["child"] = child
    if invocation_id is not None:
        call["invocation_id"] = invocation_id
    return {"tool_call": call}


def _run(tmp_path, payload, nonce="rt-test", name=None):
    env_path = _envelope_file(tmp_path, payload, name=name or "envelope.json")
    artifacts = str(tmp_path / "artifacts.json")
    proc = subprocess.run([sys.executable, conftest.LIVE, "--envelope", env_path,
                           "--artifacts", artifacts, "--nonce", nonce],
                          capture_output=True, text=True, cwd=conftest.ROOT)
    doc = json.loads(open(artifacts, encoding="utf-8").read()) if os.path.exists(artifacts) \
        else {}
    return proc, doc


def _latest(doc):
    return doc["runs"][-1] if doc.get("runs") else {}


# --------------------------------------------------------------- the positive side
def test_accepted_envelope_forks_a_real_child(tmp_path):
    proc, doc = _run(tmp_path, _payload(), nonce="abcdef12")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    run = _latest(doc)
    assert run["roundtrip_nonce"] == "abcdef12"
    assert run["nested"]["decision"]["reason"] == n2_resolve.OK
    assert run["nested"]["decision"]["canonical_name"] == n2_canonical.CANONICAL_LIVE_ENTRY
    dispatch = run["nested"]["dispatch"]
    assert dispatch["attempted"] is True
    assert dispatch["returncode"] == 0
    observed = dispatch["observed"]
    harness_pid = run["top_level"]["harness_pid"]
    assert observed["pid"] != harness_pid
    assert observed["ppid"] == harness_pid, "the child must be OUR child"
    assert observed["nonce"] == "abcdef12"
    assert observed["child_env"] == "1"
    assert observed["secret"].startswith("sec-")
    assert all(run["verdicts"].values()), run["verdicts"]
    # the nonce-bound marker is what the transcript is searched for later
    assert n2_live.TOP_MARK % "abcdef12" in proc.stdout


def test_child_secret_never_reaches_the_harness_stdout(tmp_path):
    """The transcript only sees stdout; a secret in it would void the subprocess test."""
    proc, doc = _run(tmp_path, _payload(), nonce="secres01")
    secret = _latest(doc)["nested"]["dispatch"]["observed"]["secret"]
    assert secret in open(str(tmp_path / "artifacts.json"), encoding="utf-8").read()
    assert secret not in proc.stdout, "the harness echoed the nested stdout"


def test_two_runs_append_to_the_same_artifacts_file(tmp_path):
    first = _envelope_file(tmp_path, _payload(), name="a.json")
    artifacts = str(tmp_path / "artifacts.json")
    for nonce in ("run-one", "run-two"):
        proc = subprocess.run([sys.executable, conftest.LIVE, "--envelope", first,
                               "--artifacts", artifacts, "--nonce", nonce],
                              capture_output=True, text=True, cwd=conftest.ROOT)
        assert proc.returncode == 0, proc.stdout
    doc = json.loads(open(artifacts, encoding="utf-8").read())
    assert [run["roundtrip_nonce"] for run in doc["runs"]] == ["run-one", "run-two"]


def test_artifacts_record_the_registry_they_judged_with(tmp_path):
    _run(tmp_path, _payload(), nonce="registry1")
    doc = json.loads(open(str(tmp_path / "artifacts.json"), encoding="utf-8").read())
    assert doc["canonical_live_entry"] == n2_canonical.CANONICAL_LIVE_ENTRY
    assert doc["provenance"]["canonical_live_entry"]["declared_in"]
    assert "mcp_call" in doc["registry_snapshot"]["live_wrappers"]


# ------------------------------------------------------------- the refusing sides
@pytest.mark.parametrize("name,reason", [
    ("bash", n2_resolve.E_ALIAS),
    ("BASH", n2_resolve.E_ALIAS),
    ("mcp__local__Bash", n2_resolve.E_ALIAS),
    ("mcp_call", n2_resolve.E_MALFORMED_ENVELOPE),        # wrapper input lacks toolName
    ("Tool", n2_resolve.E_WRAPPER_NOT_LIVE),
    ("mcp__agent__create_rollback_snapshot", n2_resolve.E_FABRICATED_NAME),
    ("<｜call_xxx｜>", n2_resolve.E_OPAQUE_CALL_ID),
    ("Read", n2_resolve.E_LIVE_NOT_CANONICAL),
])
def test_refused_names_exit_one_and_never_execute_the_command(tmp_path, name, reason):
    marker = tmp_path / "must-not-exist"
    payload = _payload(name=name, command="touch %s" % marker)
    proc, doc = _run(tmp_path, payload, nonce="refuse1")
    assert proc.returncode == 1, proc.stdout
    run = _latest(doc)
    assert run["nested"]["decision"]["reason"] == reason, run["nested"]["decision"]
    assert run["nested"]["dispatch"]["attempted"] is False
    assert not marker.exists(), "the harness ran the command of a refused payload"
    assert not all(run["verdicts"].values())


def test_envelopeless_payload_is_refused(tmp_path):
    proc, doc = _run(tmp_path, {"command": NESTED, "child": {"agent": "kid", "depth": 1}},
                     nonce="noenv001")
    assert proc.returncode == 1
    assert _latest(doc)["nested"]["decision"]["reason"] == n2_resolve.E_NO_ENVELOPE


def test_malformed_envelope_is_refused(tmp_path):
    proc, doc = _run(tmp_path, {"tool_call": {"name": "Bash"}}, nonce="malform1")
    assert proc.returncode == 1
    assert _latest(doc)["nested"]["decision"]["reason"] == n2_resolve.E_MALFORMED_ENVELOPE


def test_missing_envelope_file_is_a_precondition_not_a_verdict(tmp_path):
    artifacts = str(tmp_path / "artifacts.json")
    proc = subprocess.run([sys.executable, conftest.LIVE,
                           "--envelope", str(tmp_path / "nope.json"),
                           "--artifacts", artifacts, "--nonce", "absent1"],
                          capture_output=True, text=True, cwd=conftest.ROOT)
    assert proc.returncode == 2, proc.stdout
    assert "PRECONDITION" in proc.stdout
    assert not os.path.exists(artifacts), "a precondition failure must not write a run"


def test_broken_json_envelope_is_a_precondition(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{\"tool_call\": {\"name\": \"Bash\", ", encoding="utf-8")
    proc = subprocess.run([sys.executable, conftest.LIVE, "--envelope", str(path),
                           "--artifacts", str(tmp_path / "a.json"), "--nonce", "broken1"],
                          capture_output=True, text=True, cwd=conftest.ROOT)
    assert proc.returncode == 2
    assert "PRECONDITION" in proc.stdout


def test_a_failing_child_command_is_reported_not_hidden(tmp_path):
    """The nested command exits 7: the round trip must not read as green."""
    proc, doc = _run(tmp_path, _payload(command="python3 %s --fail 7" % conftest.NESTED_CHILD),
                     nonce="failchild")
    assert proc.returncode == 1, proc.stdout
    run = _latest(doc)
    assert run["nested"]["dispatch"]["returncode"] == 7
    assert run["verdicts"]["nested_stdout_was_piped"] is False


# ------------------------------------------------------- transcript capture wiring
def test_capture_binds_the_transcript_copy_to_a_nonce(tmp_path):
    proc, doc = _run(tmp_path, _payload(), nonce="capnonce")
    assert proc.returncode == 0
    source = tmp_path / "session.jsonl"
    source.write_text(json.dumps({"type": "user", "message": {"content": []}}) + "\n",
                      encoding="utf-8")
    dest = tmp_path / "copy.jsonl"
    artifacts = str(tmp_path / "artifacts.json")
    rc = n2_live.capture_transcript(str(source), str(dest), artifacts, "capnonce")
    assert rc == 0
    reloaded = json.loads(open(artifacts, encoding="utf-8").read())
    capture = reloaded["transcript_capture"]
    assert capture["nonce_bound_to"] == "capnonce"
    assert capture["source_sha1"] == n2_transcript.file_digest(str(dest))
    assert capture["records"] == 1
    run = [r for r in reloaded["runs"] if r["roundtrip_nonce"] == "capnonce"][0]
    assert run["transcript_capture"]["copy"] == str(dest)


def test_capture_of_an_unknown_nonce_stays_at_the_top_level(tmp_path):
    proc, _doc = _run(tmp_path, _payload(), nonce="capother")
    assert proc.returncode == 0
    source = tmp_path / "session.jsonl"
    source.write_text(json.dumps({"type": "user"}) + "\n", encoding="utf-8")
    artifacts = str(tmp_path / "artifacts.json")
    rc = n2_live.capture_transcript(str(source), str(tmp_path / "copy.jsonl"), artifacts,
                                   "nonce-never-run")
    assert rc == 0
    reloaded = json.loads(open(artifacts, encoding="utf-8").read())
    assert reloaded["transcript_capture"]["nonce_bound_to"] == "nonce-never-run"
    assert all("transcript_capture" not in run for run in reloaded["runs"]), \
        "a capture must not be retro-fitted onto an unrelated run"


def test_capture_missing_source_is_a_precondition(tmp_path):
    assert n2_live.capture_transcript(str(tmp_path / "gone.jsonl"),
                                      str(tmp_path / "copy.jsonl"),
                                      str(tmp_path / "a.json"), "x") == 2


# ------------------------------------------- the REAL round trip against the host
def _real_doc():
    if not os.path.exists(conftest.ARTIFACTS):
        pytest.skip("no .n2-artifacts.json: the live round trip has not run yet")
    return json.loads(open(conftest.ARTIFACTS, encoding="utf-8").read())


def test_real_roundtrip_artifact_is_self_consistent():
    doc = _real_doc()
    assert doc["runs"], "artifacts file exists but records no run"
    run = doc["runs"][-1]
    envelope = run["nested"]["envelope"]
    name = envelope["tool_call"]["name"]
    assert n2_canonical.canonicalize(name) == n2_canonical.CANONICAL_LIVE_ENTRY
    # do not trust the stored verdict: re-judge the recorded envelope
    assert n2_resolve.reason_of(envelope) == n2_resolve.OK, run["nested"]["decision"]
    observed = run["nested"]["dispatch"]["observed"]
    harness_pid = run["top_level"]["harness_pid"]
    assert observed["pid"] != harness_pid and observed["ppid"] == harness_pid
    assert run["nested"]["dispatch"]["returncode"] == 0
    assert run["nested"]["dispatch"]["captured_by_pipe"] is True


def test_real_roundtrip_appears_in_the_captured_transcript():
    doc = _real_doc()
    if not os.path.exists(conftest.TRANSCRIPT):
        pytest.skip("no transcript copy: run `python3 src/n2_live.py --capture-transcript`")
    run = doc["runs"][-1]
    nonce = run["roundtrip_nonce"]
    records, _errors = n2_transcript.load_transcript(conftest.TRANSCRIPT)
    hits = n2_transcript.find_call_by_substring(
        records, n2_canonical.CANONICAL_LIVE_ENTRY, [nonce])
    assert hits, "the live %s call carrying nonce %s is not in the transcript" % (
        n2_canonical.CANONICAL_LIVE_ENTRY, nonce)
    marker = n2_live.TOP_MARK % nonce
    executed = [h for h in hits if marker in (h.get("result_text") or "")]
    assert executed, "the call was logged but never echoed %r" % marker
    secret = run["nested"]["dispatch"]["observed"]["secret"]
    assert secret not in json.dumps([h["result_text"] for h in executed]), \
        "the child secret leaked into the top-level stdout"
