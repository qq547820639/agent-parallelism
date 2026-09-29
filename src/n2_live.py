#!/usr/bin/env python3
"""Live round trip: one top-level tool call, one nested declarative tool_call inside it.

RUN: python3 src/n2_live.py --envelope .n2-live/nested_tool_call.json [--nonce N]
       python3 src/n2_live.py --capture-transcript --transcript-source PATH [--nonce N]

Stdlib only.  No network.  Writes one artifacts file (default `<repo>/.n2-artifacts.json`)
and a transcript copy (default `<repo>/.n2-transcript.jsonl`).  Exit codes are
three-valued and `checks/n2_check.py` relies on the split:

    0  the round trip happened: envelope accepted AND the nested call ran in a child
    1  the judge refused the payload, or the child ran but broke the subprocess rules
    2  precondition unmet (envelope file unreadable, JSON broken, dispatch timed out)

Both sides, in one round trip
----------------------------
Side A (top level): the host itself executes this script through its canonical live
entry (`Bash`) and records the call plus its stdout in the transcript.  This script
prints `N2-TOP-<nonce>` so there is a nonce-bound marker only a real host call can
put into the transcript's tool_result.

Side B (nested): the same top-level invocation carries a declarative `tool_call`
block (the `--envelope` file, written by the very same host call).  It is judged by
the pure resolver in n2_resolve.py and, if accepted, dispatched by
`subprocess.run(..., capture_output=True)` -- so the nested result arrives through a
pipe from another process, carrying a secret that only that process generated.

Nothing here fakes a tool call: this module never calls a host tool, it only runs
subprocesses and writes files.  The host-level half of the evidence is the
transcript, which `--capture-transcript` snapshots next to the artifacts.
"""

import argparse
import datetime
import json
import os
import platform
import secrets
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import n2_canonical                      # noqa: E402
import n2_envelope                       # noqa: E402
import n2_resolve                        # noqa: E402

SCHEMA = "n2-artifacts/1"
DEFAULT_ARTIFACTS = os.path.join(ROOT, ".n2-artifacts.json")
DEFAULT_TRANSCRIPT_COPY = os.path.join(ROOT, ".n2-transcript.jsonl")
TOP_MARK = "N2-TOP-%s"
DISPATCH_TIMEOUT = 60


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def load_artifacts(path):
    if not os.path.exists(path):
        return {"schema": SCHEMA, "repo_root": ROOT,
                "canonical_live_entry": n2_canonical.CANONICAL_LIVE_ENTRY,
                "provenance": n2_canonical.PROVENANCE,
                "registry_snapshot": {
                    "direct_live": sorted(n2_canonical.DIRECT_LIVE_TOOLS),
                    "live_mcp_servers": sorted(n2_canonical.LIVE_MCP_SERVERS),
                    "live_wrappers": sorted(n2_canonical.LIVE_WRAPPERS),
                    "wrapper_reach": dict(n2_canonical.WRAPPER_REACH),
                },
                "runs": []}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            doc = json.load(handle)
    except ValueError as exc:
        raise SystemExit("PRECONDITION artifacts file %s is not valid JSON: %s" % (path, exc))
    if not isinstance(doc, dict) or not isinstance(doc.get("runs"), list):
        raise SystemExit("PRECONDITION artifacts file %s has no `runs` list" % path)
    return doc


def save_artifacts(path, doc):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


class NotDispatched(Exception):
    """Raised when dispatch is asked to run a payload the judge refused.

    `run_roundtrip` already branches on the verdict, so this cannot fire on the normal
    path.  It exists because a dispatcher that runs whatever command is in front of it
    is one refactored call away from executing a refused nested payload's command line.
    """


def as_payload(envelope):
    """Rebuild the declarative payload from a parsed `n2_envelope.Envelope`."""
    call = {"name": envelope.name, "input": envelope.input}
    if envelope.invocation_id is not None:
        call["invocation_id"] = envelope.invocation_id
    if envelope.child is not None:
        call["child"] = envelope.child
    return {"tool_call": call}


def dispatch_nested(envelope, nonce, cwd=ROOT, registry=None):
    """Run the accepted nested payload as a real child process and report what came back.

    Re-judges the envelope on its way past the gate (`n2_resolve.evaluate`) and raises
    `NotDispatched` unless the judge says pass, so no caller can hand this the command
    string of a refused payload.
    """
    decision = n2_resolve.evaluate(as_payload(envelope), registry=registry)
    if decision.verdict != "pass":
        raise NotDispatched("%s: %s" % (decision.reason, decision.detail))
    command = envelope.input.get("command")
    argv = ["/bin/sh", "-c", command]
    env = dict(os.environ)
    env["N2_NONCE"] = nonce
    env["N2_CHILD"] = "1"
    started = time.time()
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              env=env, timeout=DISPATCH_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"attempted": True, "argv": argv, "timeout_after": DISPATCH_TIMEOUT}, 2
    except OSError as exc:
        return {"attempted": True, "argv": argv, "spawn_error": str(exc)}, 1
    observed = None
    parse_error = None
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            candidate = json.loads(line)
        except ValueError as exc:
            parse_error = "%s: %r" % (exc, line[:120])
            continue
        if isinstance(candidate, dict) and candidate.get("n2_child") == "1":
            observed = candidate
    report = {
        "attempted": True,
        "argv": argv,
        "cwd": cwd,
        "returncode": proc.returncode,
        "stdout_bytes": len(proc.stdout or ""),
        "stderr_tail": (proc.stderr or "")[-400:],
        "elapsed_sec": round(time.time() - started, 3),
        "captured_by_pipe": True,
        "harness_pid": os.getpid(),
        "observed": observed,
        "line_parse_error": parse_error,
    }
    return report, 0


def evaluate_nested_side(dispatch, decision, harness_pid):
    """The three facts the brief asks the check to observe, computed not asserted."""
    observed = dispatch.get("observed") or {}
    checks = {}
    checks["nested_envelope_valid"] = decision.verdict == "pass"
    checks["nested_wrapper_uses_canonical_name"] = (
        decision.diagnostics.get("canonical_name") == n2_canonical.CANONICAL_LIVE_ENTRY)
    checks["nested_ran_in_subprocess"] = bool(observed) and observed.get("pid") not in (
        None, harness_pid)
    checks["nested_child_parent_is_harness"] = observed.get("ppid") == harness_pid
    checks["nested_secret_only_in_child_report"] = bool(observed.get("secret"))
    checks["nested_stdout_was_piped"] = (dispatch.get("captured_by_pipe") is True
                                        and dispatch.get("returncode") == 0)
    return checks


def top_level_evidence(nonce, argv, artifacts_path, envelope_path):
    return {
        "entry": n2_canonical.CANONICAL_LIVE_ENTRY,
        "nonce": nonce,
        "stdout_marker": TOP_MARK % nonce,
        "harness_pid": os.getpid(),
        "harness_ppid": os.getppid(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "argv": list(argv),
        "cwd": os.getcwd(),
        "envelope_path": envelope_path,
        "artifacts_path": artifacts_path,
        "declared_in": n2_canonical.PROVENANCE["canonical_live_entry"]["declared_in"],
    }


def print_report(run, artifacts_path):
    """Human-readable summary.  The child secret is redacted on purpose: it must not
    reach the top-level stdout, or the transcript would hold it and the
    "came from a subprocess" test would be vacuous."""
    nested = run["nested"]
    decision = nested["decision"]
    dispatch = nested["dispatch"]
    observed = dispatch.get("observed") or {}
    secret = observed.get("secret")
    redacted = dict(observed)
    if secret:
        redacted["secret"] = "%s…(%d chars, redacted on stdout)" % (
            secret[:4], len(secret))
    print("== n2 live round trip ==")
    print("generated_at        : %s" % run["generated_at"])
    print("roundtrip_nonce     : %s" % run["roundtrip_nonce"])
    print("canonical live entry: %s" % run["canonical_live_entry"])
    print("top level           : %s  pid=%s ppid=%s" % (
        run["top_level"]["stdout_marker"], run["top_level"]["harness_pid"],
        run["top_level"]["harness_ppid"]))
    print("nested envelope     : %s" % json.dumps(nested["envelope"], sort_keys=True)[:220])
    print("nested decision     : %s" % decision["evidence"])
    print("nested dispatch     : rc=%s argv=%s" % (
        dispatch.get("returncode"), dispatch.get("argv")))
    print("nested child report : %s" % json.dumps(redacted, sort_keys=True))
    print("verdicts            :")
    for key in sorted(run["verdicts"]):
        print("   %-40s %s" % (key, run["verdicts"][key]))
    print("artifacts           : %s" % artifacts_path)


def run_roundtrip(envelope_path, artifacts_path, nonce=None):
    if not os.path.exists(envelope_path):
        print("PRECONDITION no envelope file at %s" % envelope_path)
        return 2
    try:
        with open(envelope_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except ValueError as exc:
        print("PRECONDITION envelope %s is not valid JSON: %s" % (envelope_path, exc))
        return 2

    nonce = nonce or secrets.token_hex(4)
    decision = n2_resolve.evaluate(payload)
    parsed = n2_envelope.parse_envelope(payload, canonicalize=n2_canonical.canonicalize)
    harness_pid = os.getpid()
    if decision.verdict == "pass" and isinstance(parsed, n2_envelope.Envelope):
        dispatch, dispatch_rc = dispatch_nested(parsed, nonce)
    else:
        dispatch, dispatch_rc = {"attempted": False,
                                 "reason": "nested call not dispatched (decision=%s)"
                                           % decision.reason}, 1

    verdicts = evaluate_nested_side(dispatch, decision, harness_pid)
    run = {
        "generated_at": _now(),
        "roundtrip_nonce": nonce,
        "canonical_live_entry": n2_canonical.CANONICAL_LIVE_ENTRY,
        "top_level": top_level_evidence(nonce, sys.argv, artifacts_path, envelope_path),
        "nested": {
            "envelope": payload,
            "decision": decision.as_dict(),
            "dispatch": dispatch,
        },
        "verdicts": verdicts,
        "dispatch_rc": dispatch_rc,
    }
    doc = load_artifacts(artifacts_path)
    doc["updated_at"] = _now()
    doc["runs"].append(run)
    save_artifacts(artifacts_path, doc)

    print(TOP_MARK % nonce)              # nonce-bound marker for the transcript
    print_report(run, artifacts_path)
    if dispatch_rc == 2:
        print("RESULT: PRECONDITION UNMET (nested dispatch could not be attempted)")
        return 2
    if all(verdicts.values()) and decision.verdict == "pass":
        print("RESULT: ALL PASS")
        return 0
    print("RESULT: FAILURES PRESENT")
    for key in sorted(verdicts):
        if not verdicts[key]:
            print("  FAIL  verdict %s" % key)
    if decision.verdict != "pass":
        print("  FAIL  envelope refused: %s" % decision.evidence())
    return 1


def capture_transcript(source, dest, artifacts_path, nonce=None):
    """Snapshot the live session transcript next to the artifacts, with provenance."""
    if not source:
        print("PRECONDITION --transcript-source is required for --capture-transcript")
        return 2
    if not os.path.exists(source):
        print("PRECONDITION transcript not found: %s" % source)
        return 2
    import shutil
    import n2_transcript
    records, errors = n2_transcript.load_transcript(source)
    digest = n2_transcript.file_digest(source)
    size = os.path.getsize(source)
    shutil.copyfile(source, dest)
    doc = load_artifacts(artifacts_path)
    doc["transcript_capture"] = {
        "captured_at": _now(),
        "source": os.path.abspath(source),
        "copy": os.path.abspath(dest),
        "source_bytes": size,
        "source_sha1": digest,
        "records": len(records),
        "unparseable_lines": len(errors),
        "tool_uses": len(n2_transcript.tool_calls(records)),
        "nonce_bound_to": nonce,
        "default_check_path": DEFAULT_TRANSCRIPT_COPY,
    }
    if nonce:
        for run in doc["runs"]:
            if run.get("roundtrip_nonce") == nonce:
                run["transcript_capture"] = doc["transcript_capture"]
    doc["updated_at"] = _now()
    save_artifacts(artifacts_path, doc)
    print("== transcript capture ==")
    print("source      : %s (%d bytes, sha1 %s)" % (os.path.abspath(source), size, digest[:12]))
    print("copy        : %s" % os.path.abspath(dest))
    print("records     : %d (unparseable lines %d)" % (len(records), len(errors)))
    print("tool_uses   : %d" % doc["transcript_capture"]["tool_uses"])
    print("nonce       : %s" % nonce)
    print("artifacts   : %s" % artifacts_path)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--envelope", help="path to the nested declarative tool_call JSON")
    parser.add_argument("--artifacts", default=DEFAULT_ARTIFACTS)
    parser.add_argument("--nonce", default=None)
    parser.add_argument("--capture-transcript", action="store_true",
                        help="snapshot a transcript into the default check path")
    parser.add_argument("--transcript-source", default=None)
    parser.add_argument("--transcript-copy", default=DEFAULT_TRANSCRIPT_COPY)
    opts = parser.parse_args(argv)

    if opts.capture_transcript:
        return capture_transcript(opts.transcript_source, opts.transcript_copy,
                                 opts.artifacts, opts.nonce)
    if not opts.envelope:
        print("PRECONDITION --envelope PATH is required (or use --capture-transcript)")
        return 2
    return run_roundtrip(opts.envelope, opts.artifacts, opts.nonce)


if __name__ == "__main__":
    sys.exit(main())
