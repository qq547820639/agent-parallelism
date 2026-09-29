#!/usr/bin/env python3
"""n2 closed-world gate: did a nested declarative tool_call really go through the host?

RUN THIS SCRIPT DIRECTLY; do not read it into your context.
Stdlib only.  No network.  Read-only except `--self-test`, which writes into a
temp dir it makes itself.  Python 3.9+.

    python3 checks/n2_check.py                     # the gate
    python3 checks/n2_check.py --verbose           # per-check evidence lines
    python3 checks/n2_check.py --self-test         # prove every gate can fire
    N2_TRANSCRIPT=/path/to/session.jsonl python3 checks/n2_check.py

Inputs
------
transcript : $N2_TRANSCRIPT, else --transcript PATH, else <repo>/.n2-transcript.jsonl
             (harness artifacts record where they copied it from)
artifacts  : --artifacts PATH, else <repo>/.n2-artifacts.json
probes     : scripts/malformed_probe_cases.py (imported, never trusted for verdicts)

Exit codes are three-valued and the split matters:
    0  every gate passed
    1  at least one gate failed -- a real closed-world violation, names printed below
    2  precondition unmet (unreadable transcript / artifacts): NOT a verdict on the run

No transcript path is baked in: nothing in this file knows where my session lives.
The gates only ever read the transcript the harness pointed them at.
"""

import argparse
import ast
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
SCRIPTS = os.path.join(ROOT, "scripts")
for _path in (SRC, SCRIPTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import n2_canonical                       # noqa: E402
import n2_resolve                         # noqa: E402
import n2_transcript                      # noqa: E402

DEFAULT_TRANSCRIPT = os.path.join(ROOT, ".n2-transcript.jsonl")
DEFAULT_ARTIFACTS = os.path.join(ROOT, ".n2-artifacts.json")
CORPUS_PATH = os.path.join(SCRIPTS, "malformed_probe_cases.py")

REQUIRED_SHAPES = ("direct", "aliased", "wrapped", "fabricated", "no_envelope",
                   "malformed_envelope")
MIN_PROBES = 4
LIVE_ROSTER_TOOL = "mcp_list"


class Bundle(object):
    """Everything the gates read.  Real runs load it from disk; --self-test builds it."""

    def __init__(self, records, artifacts, transcript_path=None, corpus=None,
                 corpus_path=CORPUS_PATH):
        self.records = records
        self.artifacts = artifacts
        self.transcript_path = transcript_path
        self.corpus_path = corpus_path
        self.corpus = corpus if corpus is not None else load_corpus(corpus_path)

    @property
    def runs(self):
        runs = self.artifacts.get("runs") if isinstance(self.artifacts, dict) else None
        return runs if isinstance(runs, list) else []

    def latest_run(self):
        return self.runs[-1] if self.runs else {}

    def run_for_nonce(self, nonce):
        if nonce:
            for run in reversed(self.runs):
                if run.get("roundtrip_nonce") == nonce:
                    return run
            return {}
        return self.latest_run()


def load_corpus(path=None):
    """Import the probe corpus module and return its cases (verdicts not trusted)."""
    import importlib.util
    path = path or CORPUS_PATH
    spec = importlib.util.spec_from_file_location("malformed_probe_cases_" + str(abs(hash(path))),
                                                 path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.cases()


# ---------------------------------------------------------------------------
# gates over the probe corpus
# ---------------------------------------------------------------------------
def gate_corpus_covers_each_shape(bundle):
    corpus = bundle.corpus
    shapes = {}
    for case in corpus:
        shapes.setdefault(case.get("shape"), []).append(case.get("id"))
    missing = [shape for shape in REQUIRED_SHAPES if not shapes.get(shape)]
    direct = [c for c in corpus if c.get("expected_verdict") == "pass"]
    failing = [c for c in corpus if c.get("expected_verdict") == "fail"]
    detail = ("probes=%d per-shape=%s pass=%d fail=%d"
              % (len(corpus), {k: len(v) for k, v in sorted(shapes.items())},
                 len(direct), len(failing)))
    if len(corpus) < MIN_PROBES:
        return False, "only %d probes, brief requires >= %d (%s)" % (len(corpus), MIN_PROBES, detail)
    if missing:
        return False, "no probe for shape(s) %s (%s)" % (missing, detail)
    if not direct or not failing:
        return False, ("corpus has no %s case, so it can rubber-stamp any validator (%s)"
                       % ("pass" if not direct else "fail", detail))
    return True, detail


def gate_expectations_are_literal_and_reasoned(bundle):
    """No hardcoded PASS: expectations must be literal verdict+reason data per probe."""
    tree = ast.parse(open(bundle.corpus_path, "r", encoding="utf-8").read())
    assign = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "PROBE_CASES":
                    assign = node
    if assign is None:
        return False, "PROBE_CASES assignment not found in %s" % bundle.corpus_path
    if not isinstance(assign.value, (ast.List, ast.Tuple)):
        return False, "PROBE_CASES is not a literal sequence (it is computed)"
    bad = []
    reasons = set()
    ids = []
    n_pass = n_fail = 0
    for element in assign.value.elts:
        if not isinstance(element, ast.Dict):
            bad.append("a case is not a dict literal")
            continue
        fields = {}
        for key, value in zip(element.keys, element.values):
            if isinstance(key, ast.Constant):
                fields[key.value] = value
        if isinstance(fields.get("id"), ast.Constant):
            ids.append(fields["id"].value)
        for field in ("id", "shape", "payload", "expected_verdict", "expected_reason", "basis"):
            if field not in fields:
                bad.append("%s lacks %r" % (_case_id(fields), field))
        verdict = fields.get("expected_verdict")
        reason = fields.get("expected_reason")
        if not isinstance(verdict, ast.Constant) or verdict.value not in ("pass", "fail"):
            bad.append("%s: expected_verdict is not a literal pass|fail" % _case_id(fields))
        elif verdict.value == "pass":
            n_pass += 1
        else:
            n_fail += 1
        if not isinstance(reason, ast.Constant) or not isinstance(reason.value, str) \
                or not reason.value:
            bad.append("%s: expected_reason is not a literal non-empty string" % _case_id(fields))
        else:
            reasons.add(reason.value)
        payload = fields.get("payload")
        if not isinstance(payload, (ast.Dict, ast.Str, ast.List, ast.Constant)):
            bad.append("%s: payload is computed, so expectations could depend on the code"
                       % _case_id(fields))
        for name in ast.walk(payload if payload is not None else ast.Dict(keys=[], values=[])):
            if isinstance(name, ast.Name) and name.id.startswith("n2_"):
                bad.append("%s: payload reads the implementation (%s)"
                           % (_case_id(fields), name.id))
    if bad:
        return False, "; ".join(bad[:6])
    seen = set()
    dupes = set()
    for probe_id in ids:
        if probe_id in seen:
            dupes.add(probe_id)
        seen.add(probe_id)
    if dupes:
        return False, ("probe ids repeat, so one expectation can hide behind another: %s"
                       % sorted(dupes))
    if n_fail == 0:
        return False, "every probe expects pass: the corpus is a rubber stamp"
    if "OK" in reasons and len(reasons) < 2:
        return False, "only one reason code (%s) in the corpus" % reasons
    unknown = sorted(r for r in reasons if r not in n2_resolve.REASONS)
    if unknown:
        return False, "reasons not known to the resolver: %s" % unknown
    return True, ("literal expectations: %d pass / %d fail, %d distinct reason codes %s"
                  % (n_pass, n_fail, len(reasons), sorted(reasons)))


def _case_id(fields):
    node = fields.get("id")
    if isinstance(node, ast.Constant):
        return str(node.value)
    return "<unknown id>"


def gate_validator_matches_expectations(bundle):
    mismatches = []
    for case in bundle.corpus:
        decision = n2_resolve.evaluate(case["payload"])
        if decision.verdict != case["expected_verdict"] or decision.reason != case["expected_reason"]:
            mismatches.append("%s: want %s/%s got %s" % (
                case["id"], case["expected_verdict"], case["expected_reason"],
                decision.evidence()))
    if mismatches:
        return False, "%d of %d probes disagree:\n        %s" % (
            len(mismatches), len(bundle.corpus), "\n        ".join(mismatches[:5]))
    return True, "%d probes re-judled by src/n2_resolve.py, verdict AND reason match" % len(
        bundle.corpus)


def gate_direct_passes_and_others_fail(bundle):
    """The brief's headline rule, judged by what the VALIDATOR does per shape.

    Comparing the validator against each case's own expectation would read the
    expectation table as the answer key: relabel an alias probe as `direct`, keep
    expecting `fail`, and this gate stays green while the brief's headline rule --
    direct passes, everything else fails -- silently goes untested.
    """
    direct = [c for c in bundle.corpus if c["shape"] == "direct"]
    others = [c for c in bundle.corpus if c["shape"] != "direct"]
    refused_direct = [c["id"] for c in direct
                      if n2_resolve.verdict_of(c["payload"]) != "pass"]
    waved_through = [c["id"] for c in others
                     if n2_resolve.verdict_of(c["payload"]) != "fail"]
    if refused_direct or waved_through:
        return False, "direct cases the validator refused: %s | other cases it passed: %s" % (
            refused_direct, waved_through)
    if not direct or not others:
        return False, "corpus has %d direct and %d other cases; the rule needs both sides" % (
            len(direct), len(others))
    return True, ("validator passes all %d direct cases and fails all %d others "
                  "(expectations agree on every probe)"
                  % (len(direct), len(others)))


# ---------------------------------------------------------------------------
# gates over the registry and the live round trip
# ---------------------------------------------------------------------------
def gate_canonical_entry_declared(bundle):
    entry = n2_canonical.CANONICAL_LIVE_ENTRY
    prov = n2_canonical.PROVENANCE.get("canonical_live_entry", {})
    cls, _target = n2_canonical.DEFAULT_REGISTRY.classify(entry)
    if not entry or cls != n2_canonical.CANONICAL:
        return False, "canonical entry %r classifies as %r" % (entry, cls)
    if not prov.get("declared_in") or not prov.get("input_shape"):
        return False, "PROVENANCE records no discovery source for the canonical entry"
    rejected = n2_canonical.PROVENANCE.get("rejected_alternative", {})
    return True, "entry=%r declared_in=%r rejected_alternative=%r (%s)" % (
        entry, prov["declared_in"][:48], rejected.get("value"),
        "host docs read this session")


def gate_canonical_entry_confirmed_by_real_call(bundle):
    entry = n2_canonical.CANONICAL_LIVE_ENTRY
    calls = n2_transcript.calls_named(bundle.records, entry)
    paired = [c for c in n2_transcript.paired_calls(bundle.records) if c["name"] == entry]
    if not calls:
        return False, "transcript has no tool_use named %r" % entry
    if not paired:
        return False, ("%d tool_use records named %r but none has a paired tool_result "
                       "(calls were logged, never executed)" % (len(calls), entry))
    with_output = [c for c in paired if (c.get("result_text") or "").strip()]
    if not with_output:
        return False, "paired results are all empty for %r" % entry
    return True, ("entry %r confirmed by %d executed live call(s) with non-empty results, "
                  "e.g. tool_use_id=%s at record %d"
                  % (entry, len(with_output), with_output[0]["tool_use_id"],
                     with_output[0]["result_index"]))


def gate_roster_read_from_host(bundle):
    """The 'fabricated/absent' verdicts lean on the MCP roster: was it really read?"""
    calls = [c for c in n2_transcript.paired_calls(bundle.records)
             if c["name"] == LIVE_ROSTER_TOOL]
    if not calls:
        return False, ("no executed %r call in the transcript, so the server set is a claim, "
                       "not a readout" % LIVE_ROSTER_TOOL)
    text = calls[-1]["result_text"] or ""
    missing = [s for s in n2_canonical.LIVE_MCP_SERVERS if s not in text]
    if missing:
        return False, ("roster read does not mention declared server(s) %s" % missing)
    if "mcp__agent__" in text:
        return False, ("the roster DOES expose mcp__agent__ tools, so the corpus' fabricated "
                       "cases are not fabricated here")
    return True, ("%s executed at record %d; %d declared server(s) present in its result; "
                  "no mcp__agent__ tool in it"
                  % (LIVE_ROSTER_TOOL, calls[-1]["result_index"],
                     len(n2_canonical.LIVE_MCP_SERVERS)))


def gate_run_recorded(bundle):
    run = bundle.latest_run()
    if not run:
        return False, "artifacts contain no runs"
    nonce = run.get("roundtrip_nonce")
    if not nonce:
        return False, "latest run has no roundtrip_nonce"
    return True, "run nonce=%s generated_at=%s" % (nonce, run.get("generated_at"))


def gate_live_call_present_in_transcript(bundle):
    """The round trip must be a real host call, not a line in an artifacts file."""
    run = bundle.latest_run()
    nonce = (run.get("roundtrip_nonce") or "").strip()
    entry = run.get("canonical_live_entry") or n2_canonical.CANONICAL_LIVE_ENTRY
    if not nonce:
        return False, "no nonce to bind the transcript to"
    marker = "N2-TOP-%s" % nonce
    hits = n2_transcript.find_call_by_substring(bundle.records, entry, [nonce])
    if not hits:
        return False, ("no executed %s call whose input mentions nonce %s -- the harness ran, "
                       "but not as a host tool call" % (entry, nonce))
    echoed = [h for h in hits if marker in (h.get("result_text") or "")]
    if not echoed:
        return False, ("nonce %s appears in %d call(s) but no tool_result carries %r, so the "
                       "call never produced output" % (nonce, len(hits), marker))
    hit = echoed[-1]
    return True, ("live %s call tool_use_id=%s (record %d, result record %d) echoed %r"
                  % (entry, hit["tool_use_id"], hit["index"], hit["result_index"], marker))


def gate_nested_wrapper_uses_canonical_name(bundle):
    run = bundle.latest_run()
    envelope = (run.get("nested") or {}).get("envelope")
    if not isinstance(envelope, dict):
        return False, "run records no nested envelope"
    decision = n2_resolve.evaluate(envelope)
    stored = (run.get("nested") or {}).get("decision") or {}
    canonical = n2_canonical.canonicalize(
        (envelope.get("tool_call") or {}).get("name"))
    problems = []
    if decision.verdict != "pass":
        problems.append("re-judged as %s" % decision.evidence())
    if canonical != n2_canonical.CANONICAL_LIVE_ENTRY:
        problems.append("wrapper name canonicalises to %r, not the canonical entry" % canonical)
    if stored.get("reason") != decision.reason:
        problems.append("harness stored reason=%r but the resolver now says %r"
                        % (stored.get("reason"), decision.reason))
    if (envelope.get("tool_call") or {}).get("child", {}).get("depth") != 1:
        problems.append("nested payload does not declare depth=1")
    if problems:
        return False, "; ".join(problems)
    return True, ("nested tool_call wrapped in %r (canonicalised from %r), resolver agrees "
                  "with the harness (%s)"
                  % (canonical, (envelope.get("tool_call") or {}).get("name"), decision.reason))


def gate_nested_envelope_wrapped_in_the_live_call(bundle):
    """Both sides in ONE round trip: the declarative block rode inside the live call."""
    run = bundle.latest_run()
    nonce = run.get("roundtrip_nonce") or ""
    entry = n2_canonical.CANONICAL_LIVE_ENTRY
    envelope = ((run.get("nested") or {}).get("envelope") or {})
    invocation_id = ((envelope.get("tool_call") or {}).get("invocation_id")) or ""
    if not invocation_id:
        return False, "nested envelope carries no invocation_id to bind with"
    hits = n2_transcript.find_call_by_substring(bundle.records, entry, [nonce, invocation_id])
    # Search the arguments as TEXT: json.dumps() of the input escapes the block's own
    # quotes, so a '"tool_call"' probe against the serialized form never matches and the
    # gate would read a real round trip as absent.
    name_forms = ('"name": "%s"' % entry, '"name":"%s"' % entry)
    for hit in hits:
        text = " ".join(value for value in (hit.get("input") or {}).values()
                        if isinstance(value, str))
        if "tool_call" in text and invocation_id in text \
                and any(form in text for form in name_forms):
            return True, ("the live %s call %s carries the tool_call block naming %r with "
                          "invocation_id=%r (record %d): top level and nested in ONE round trip"
                          % (entry, hit["tool_use_id"], entry, invocation_id, hit["index"]))
    return False, ("no single %s call carries the nonce %r, invocation_id %r and a "
                   "`tool_call` block naming %r (%d candidate call(s) examined)"
                   % (entry, nonce, invocation_id, entry, len(hits)))


def gate_nested_result_from_subprocess(bundle):
    run = bundle.latest_run()
    nested = run.get("nested") or {}
    dispatch = nested.get("dispatch") or {}
    observed = dispatch.get("observed") or {}
    harness_pid = (run.get("top_level") or {}).get("harness_pid")
    problems = []
    if not dispatch.get("attempted"):
        problems.append("nested call was never dispatched (%s)" % dispatch.get("reason"))
    if not observed:
        problems.append("no child report parsed from the nested stdout (%s)"
                        % dispatch.get("line_parse_error"))
    child_pid = observed.get("pid")
    if child_pid is None:
        problems.append("child report has no pid")
    elif child_pid == harness_pid:
        problems.append("nested pid == harness pid %r: same process, not a subprocess"
                        % harness_pid)
    if observed.get("ppid") != harness_pid:
        problems.append("child's ppid=%r is not the harness pid %r"
                        % (observed.get("ppid"), harness_pid))
    if observed.get("child_env") != "1":
        problems.append("child did not inherit the dispatch env (N2_CHILD=%r)"
                        % observed.get("child_env"))
    if observed.get("nonce") != run.get("roundtrip_nonce"):
        problems.append("child nonce %r != run nonce %r"
                        % (observed.get("nonce"), run.get("roundtrip_nonce")))
    if dispatch.get("returncode") != 0:
        problems.append("nested process exited %r" % dispatch.get("returncode"))
    if not observed.get("secret"):
        problems.append("child reported no secret, so pipe capture is unverifiable")
    if problems:
        return False, "; ".join(problems)
    return True, ("nested ran as pid=%s (ppid=%s=harness), exit 0, stdout came through a pipe "
                  "(%d bytes), child-generated secret %r"
                  % (child_pid, harness_pid, dispatch.get("stdout_bytes", 0),
                     observed["secret"][:8] + "…"))


def gate_secret_never_crossed_the_top_level(bundle):
    """If the nested stdout had been echoed to the top level, the transcript would hold it."""
    run = bundle.latest_run()
    secret = (((run.get("nested") or {}).get("dispatch") or {}).get("observed") or {}).get("secret")
    if not secret:
        return False, "no child secret recorded to test"
    paired = n2_transcript.paired_calls(bundle.records)
    if not paired:
        return False, ("transcript has no executed call to scan, so \"absent from the "
                       "transcript\" would be vacuously true")
    if secret not in json.dumps(bundle.artifacts):
        return False, ("child secret %r is not even in the artifacts file, so it proved "
                       "nothing about pipe capture" % secret[:8] + "â¦")
    leak = [c["tool_use_id"] for c in paired
            if secret in json.dumps(c["input"], ensure_ascii=False)
            or secret in (c.get("result_text") or "")]
    if leak:
        return False, ("child secret %r appears in transcript call(s) %s, so the nested result "
                       "was echoed at the top level rather than read from a pipe"
                       % (secret[:8] + "…", leak[:3]))
    return True, ("child secret %r is in the artifacts file and in no transcript record "
                  "(%d paired calls scanned)" % (secret[:8] + "…",
                                                 len(n2_transcript.paired_calls(bundle.records))))


def gate_artifacts_bound_to_transcript(bundle):
    capture = bundle.artifacts.get("transcript_capture") or {}
    if not capture:
        return False, "artifacts have no transcript_capture block"
    nonce = bundle.latest_run().get("roundtrip_nonce")
    if capture.get("nonce_bound_to") != nonce:
        return False, ("capture bound to nonce %r but the run under test is %r"
                       % (capture.get("nonce_bound_to"), nonce))
    if not capture.get("source") or not capture.get("records"):
        return False, "capture records no source or zero records"
    if bundle.transcript_path and os.path.abspath(capture.get("copy", "")) != \
            os.path.abspath(bundle.transcript_path):
        return False, ("the transcript I read (%s) is not the one the harness captured (%s)"
                       % (bundle.transcript_path, capture.get("copy")))
    digest = n2_transcript.file_digest(bundle.transcript_path)
    if digest != capture.get("source_sha1"):
        return False, ("transcript copy changed after capture (sha1 %s != recorded %s)"
                       % (digest[:12], str(capture.get("source_sha1"))[:12]))
    return True, ("capture binds nonce %s to %s (%d records, %d tool_uses, sha1 %s matches the "
                  "file on disk)" % (nonce, os.path.basename(capture["source"]),
                                     capture["records"], capture.get("tool_uses", 0),
                                     digest[:12]))


def gate_self_test_fires(bundle):
    """A gate nobody can make red is not a gate."""
    cmd = [sys.executable, "-u", os.path.abspath(__file__), "--self-test"]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    tail = (proc.stdout or "").strip().splitlines()[-3:]
    if proc.returncode != 0:
        return False, ("--self-test exited %d: %s"
                       % (proc.returncode, " | ".join(tail)))
    return True, "--self-test rc=0: %s" % " | ".join(tail)


GATES = (
    ("probe_corpus_covers_each_shape", gate_corpus_covers_each_shape),
    ("probe_expectations_are_literal_not_hardcoded_pass", gate_expectations_are_literal_and_reasoned),
    ("probe_verdicts_match_validator", gate_validator_matches_expectations),
    ("direct_passes_and_other_shapes_fail", gate_direct_passes_and_others_fail),
    ("canonical_entry_declared_from_host_docs", gate_canonical_entry_declared),
    ("canonical_entry_confirmed_by_real_call", gate_canonical_entry_confirmed_by_real_call),
    ("mcp_roster_backed_by_real_call", gate_roster_read_from_host),
    ("live_roundtrip_run_recorded", gate_run_recorded),
    ("live_call_present_in_transcript", gate_live_call_present_in_transcript),
    ("nested_wrapper_uses_canonical_name", gate_nested_wrapper_uses_canonical_name),
    ("nested_block_inside_the_same_live_call", gate_nested_envelope_wrapped_in_the_live_call),
    ("nested_result_from_subprocess", gate_nested_result_from_subprocess),
    ("nested_child_result_not_echoed_at_top_level", gate_secret_never_crossed_the_top_level),
    ("artifacts_bound_to_captured_transcript", gate_artifacts_bound_to_transcript),
    ("check_can_fire_on_synthetics", gate_self_test_fires),
)


SELF_GATE = "check_can_fire_on_synthetics"


def run_gates(bundle, only=None, skip=()):
    """Run the named gates.  `skip` must be honoured BEFORE calling, not filtered
    afterwards: the self-test gate spawns this script, so filtering its result after
    the fact would recurse until the box fills with subprocesses."""
    results = []
    for name, fn in GATES:
        if only and name not in only:
            continue
        if name in skip:
            continue
        try:
            ok, evidence = fn(bundle)
        except Exception as exc:                     # a crashing gate is a failed gate
            ok, evidence = False, "gate raised %s: %s" % (type(exc).__name__, exc)
        results.append((name, ok, evidence))
    return results


# --------------------------------------------------------------------------
# --self-test: minimal pairs.  Each mutation must turn exactly its own gate red,
# and the unmutated fixture must keep every gate in the pair green.  Without the
# green side a mutation could fire for an unrelated reason.
# --------------------------------------------------------------------------
def _synthetic_bundle():
    """A transcript + artifacts pair that satisfies every gate by construction."""
    entry = n2_canonical.CANONICAL_LIVE_ENTRY
    nonce = "deadbeef"
    invocation_id = "synth-1"
    envelope = {"tool_call": {"name": entry,
                              "input": {"command": "python3 src/n2_nested_child.py",
                                        "description": "nested"},
                              "invocation_id": invocation_id,
                              "child": {"agent": "n2-bench-child", "depth": 1}}}
    command = ("cat > e.json <<'JSON'\n%s\nJSON\n"
               "python3 src/n2_live.py --envelope e.json --nonce %s"
               % (json.dumps(envelope), nonce))
    harness_pid, child_pid, parent_shell_pid = 4000, 4002, 3999
    secret = "sec-synthetic000000"
    call = {"type": "assistant", "sessionId": "synth", "uuid": "a1", "isSidechain": True,
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "call_synth1", "name": entry,
                 "input": {"command": command, "description": "synthetic round trip"}}]}}
    roster_call = {"type": "assistant", "sessionId": "synth", "uuid": "a2",
                   "message": {"role": "assistant", "content": [
                       {"type": "tool_use", "id": "call_synth2", "name": LIVE_ROSTER_TOOL,
                        "input": {}}]}}
    roster_result = {"type": "user", "sessionId": "synth", "uuid": "a3",
                     "toolUseResult": {"kind": "completed"},
                     "message": {"role": "user", "content": [
                         {"type": "tool_result", "tool_use_id": "call_synth2",
                          "content": json.dumps({"tools": [
                              {"name": "mcp__node-repl__node_repl"},
                              {"name": "mcp__browser-use__list_pages"},
                              {"name": "mcp__qca__list_agents"},
                              {"name": "mcp__builtin__list_chat_sessions"},
                              {"name": "mcp__extension-market__search_extensions"},
                              {"name": "mcp__plugin_chrome-devtools-mcp_chrome-devtools__click"},
                          ], "total": 139})}]}}
    result = {"type": "user", "sessionId": "synth", "uuid": "a4",
              "toolUseResult": {"kind": "completed"},
              "message": {"role": "user", "content": [
                  {"type": "tool_result", "tool_use_id": "call_synth1",
                   "content": "N2-TOP-%s\n== n2 live round trip ==\nRESULT: ALL PASS\n" % nonce}]}}
    records = [call, roster_call, roster_result, result]
    artifacts = {
        "schema": "n2-artifacts/1",
        "runs": [{
            "generated_at": "2026-01-01T00:00:00+00:00",
            "roundtrip_nonce": nonce,
            "canonical_live_entry": entry,
            "top_level": {"harness_pid": harness_pid, "harness_ppid": parent_shell_pid,
                          "stdout_marker": "N2-TOP-%s" % nonce},
            "nested": {"envelope": envelope,
                       "decision": {"verdict": "pass", "reason": "OK",
                                    "canonical_name": entry},
                       "dispatch": {"attempted": True, "returncode": 0,
                                    "stdout_bytes": 173, "captured_by_pipe": True,
                                    "observed": {"n2_child": "1", "pid": child_pid,
                                                 "ppid": harness_pid, "secret": secret,
                                                 "nonce": nonce, "child_env": "1"}}},
            "verdicts": {"nested_envelope_valid": True},
        }],
    }
    artifacts["transcript_capture"] = _capture_block(nonce)
    return records, artifacts


def _capture_block(nonce):
    return {"captured_at": "2026-01-01T00:00:01+00:00",
            "source": os.path.join("<synthetic>", "source.jsonl"),
            "copy": "TRANSCRIPT_PATH", "source_bytes": 1, "source_sha1": "SHA1",
            "records": 4, "unparseable_lines": 0, "tool_uses": 2, "nonce_bound_to": nonce}


def _write_synthetic(tmpdir, records, artifacts):
    """Put the synthetic transcript on disk and bind the capture block to it.

    The gates that read the transcript need a real file (they hash it), so the
    fixture is written, not passed as an in-memory list -- an absence assertion
    over an unreadable file is vacuously true, and that is the failure mode here.
    """
    transcript_path = os.path.join(tmpdir, "synthetic.jsonl")
    with open(transcript_path, "w", encoding="utf-8") as handle:
        for rec in records:
            handle.write(json.dumps(rec) + "\n")
    artifacts = json.loads(json.dumps(artifacts))
    capture = artifacts.get("transcript_capture") or _capture_block(
        artifacts["runs"][0].get("roundtrip_nonce"))
    capture["copy"] = os.path.abspath(transcript_path)
    capture["source_sha1"] = n2_transcript.file_digest(transcript_path)
    capture["source_bytes"] = os.path.getsize(transcript_path)
    capture["records"] = len(records)
    artifacts["transcript_capture"] = capture
    for run in artifacts["runs"]:
        run["transcript_capture"] = capture
    return transcript_path, artifacts


MUTATIONS = {}


def mutation(name, gate, label):
    def register(fn):
        MUTATIONS[name] = (gate, label, fn)
        return fn
    return register


def _rewrite_named_call(records, rewrite):
    out = json.loads(json.dumps(records))
    for rec in out:
        for blk in _blk(rec):
            if blk.get("type") == "tool_use" and blk.get("name") == n2_canonical.CANONICAL_LIVE_ENTRY:
                cmd = (blk.get("input") or {}).get("command")
                if isinstance(cmd, str) and "deadbeef" in cmd:
                    blk["input"]["command"] = rewrite(cmd)
    return out


@mutation("no_live_call", "live_call_present_in_transcript",
          "delete the host call that ran the round trip")
def _m_no_live_call(records, artifacts, tmp):
    entry = n2_canonical.CANONICAL_LIVE_ENTRY
    kept = [r for r in records
            if not any(b.get("type") == "tool_use" and b.get("name") == entry
                       and "deadbeef" in json.dumps(b) for b in _blk(r))]
    return kept, artifacts, {}


@mutation("aliased_wrapper", "nested_wrapper_uses_canonical_name",
          "nested payload renames the entry to the alias `bash`")
def _m_aliased_wrapper(records, artifacts, tmp):
    artifacts["runs"][0]["nested"]["envelope"]["tool_call"]["name"] = "bash"
    records = _rewrite_named_call(records, lambda cmd: cmd.replace('"name": "Bash"',
                                                                  '"name": "bash"'))
    return records, artifacts, {}


@mutation("wrapper_via_live_mcp_call", "nested_wrapper_uses_canonical_name",
          "nested payload goes through the live mcp_call wrapper to reach Bash")
def _m_wrapper_mcp(records, artifacts, tmp):
    env = artifacts["runs"][0]["nested"]["envelope"]["tool_call"]
    env["name"] = "mcp_call"
    env["input"] = {"toolName": "Bash", "arguments": {"command": "x"}}
    return records, artifacts, {}


@mutation("wrapper_via_dead_tool", "nested_wrapper_uses_canonical_name",
          "nested payload goes through `Tool`, which is live nowhere")
def _m_wrapper_dead(records, artifacts, tmp):
    env = artifacts["runs"][0]["nested"]["envelope"]["tool_call"]
    env["name"] = "Tool"
    env["input"] = {"toolName": "Bash", "arguments": {"command": "x"}}
    return records, artifacts, {}


@mutation("envelopeless_nested", "nested_wrapper_uses_canonical_name",
          "nested payload drops the tool_call envelope")
def _m_envelopeless(records, artifacts, tmp):
    artifacts["runs"][0]["nested"]["envelope"] = {"command": "uname -s"}
    return records, artifacts, {}


@mutation("malformed_nested_envelope", "nested_wrapper_uses_canonical_name",
          "canonical name, but the envelope has no input object")
def _m_malformed(records, artifacts, tmp):
    env = artifacts["runs"][0]["nested"]["envelope"]["tool_call"]
    env.pop("input")
    return records, artifacts, {}


@mutation("in_process_nested", "nested_result_from_subprocess",
          "the nested result is claimed from the harness process itself")
def _m_in_process(records, artifacts, tmp):
    observed = artifacts["runs"][0]["nested"]["dispatch"]["observed"]
    observed["pid"] = artifacts["runs"][0]["top_level"]["harness_pid"]
    return records, artifacts, {}


@mutation("not_our_child", "nested_result_from_subprocess",
          "the child reports a ppid that is not the harness")
def _m_not_our_child(records, artifacts, tmp):
    artifacts["runs"][0]["nested"]["dispatch"]["observed"]["ppid"] = 999999
    return records, artifacts, {}


@mutation("nested_never_dispatched", "nested_result_from_subprocess",
          "the harness recorded a nested result it never ran")
def _m_never_dispatched(records, artifacts, tmp):
    artifacts["runs"][0]["nested"]["dispatch"] = {"attempted": False,
                                                 "reason": "skipped", "observed": {}}
    return records, artifacts, {}


@mutation("echoed_nested_stdout", "nested_child_result_not_echoed_at_top_level",
          "nested stdout is replayed into the top-level transcript result")
def _m_echoed(records, artifacts, tmp):
    secret = artifacts["runs"][0]["nested"]["dispatch"]["observed"]["secret"]
    out = json.loads(json.dumps(records))
    for rec in out:
        for blk in _blk(rec):
            if blk.get("type") == "tool_result" and blk.get("tool_use_id") == "call_synth1":
                blk["content"] = str(blk["content"]) + " " + secret
    return out, artifacts, {}


@mutation("no_roster_call", "mcp_roster_backed_by_real_call",
          "the MCP server set was asserted, never read from the host")
def _m_no_roster(records, artifacts, tmp):
    kept = [r for r in records
            if not any(b.get("type") == "tool_use" and b.get("name") == LIVE_ROSTER_TOOL
                       for b in _blk(r))]
    return kept, artifacts, {}


@mutation("roster_has_agent_server", "mcp_roster_backed_by_real_call",
          "the roster the host returned DOES carry mcp__agent__ tools")
def _m_roster_agent(records, artifacts, tmp):
    out = json.loads(json.dumps(records))
    for rec in out:
        for blk in _blk(rec):
            if blk.get("type") == "tool_result" and blk.get("tool_use_id") == "call_synth2":
                blk["content"] = str(blk["content"]).replace(
                    "mcp__node-repl__node_repl",
                    "mcp__agent__create_rollback_snapshot")
    return out, artifacts, {}


@mutation("logged_but_not_executed", "canonical_entry_confirmed_by_real_call",
          "the canonical entry\'s calls are logged but return nothing")
def _m_empty_result(records, artifacts, tmp):
    out = json.loads(json.dumps(records))
    for rec in out:
        for blk in _blk(rec):
            if blk.get("type") == "tool_result" and blk.get("tool_use_id") == "call_synth1":
                blk["content"] = ""
        if isinstance(rec.get("toolUseResult"), dict):
            rec["toolUseResult"] = {"kind": "failed"}
    return out, artifacts, {}


@mutation("transcript_edited_after_capture", "artifacts_bound_to_captured_transcript",
          "someone appended to the transcript copy after the harness hashed it")
def _m_stale_copy(records, artifacts, tmp):
    return records, artifacts, {"_post_edit_transcript": True}


@mutation("capture_of_another_run", "artifacts_bound_to_captured_transcript",
          "the capture block is bound to a different nonce")
def _m_capture_other_nonce(records, artifacts, tmp):
    artifacts["transcript_capture"]["nonce_bound_to"] = "cafebabe"
    return records, artifacts, {}


# ---- corpus-side mutations: the expectations themselves -------------------
_CORPUS_TEMPLATE = '''#!/usr/bin/env python3
"""Synthetic corpus for n2_check --self-test.  NOT the real probe file."""
CANONICAL_LIVE_ENTRY = "Bash"
PROBE_CASES = %s


def cases():
    return [dict(c) for c in PROBE_CASES]
'''

_GOOD_CASE = {
    "id": "c_good", "shape": "direct", "note": "n",
    "payload": {"tool_call": {"name": "Bash",
                              "input": {"command": "uname -s", "description": "d"},
                              "child": {"agent": "a", "depth": 1}}},
    "expected_verdict": "pass", "expected_reason": "OK", "basis": "brief clause",
}


def _write_corpus(tmp, cases):
    path = os.path.join(tmp, "synthetic_corpus.py")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(_CORPUS_TEMPLATE % json.dumps(cases, indent=4))
    return path


@mutation("rubber_stamp_corpus", "probe_expectations_are_literal_not_hardcoded_pass",
          "every probe expects pass with reason OK: a hardcoded PASS")
def _m_rubber_stamp(records, artifacts, tmp):
    cases = [dict(_GOOD_CASE, id="c_%d" % i) for i in range(6)]
    for case in cases:
        case["payload"] = {"tool_call": {"name": "mcp_call",
                                         "input": {"toolName": "Bash"},
                                         "child": {"agent": "a", "depth": 1}}}
    path = _write_corpus(tmp, cases)
    return records, artifacts, {"corpus_path": path, "corpus": cases}


@mutation("reasonless_corpus", "probe_expectations_are_literal_not_hardcoded_pass",
          "probes carry a verdict but no reason")
def _m_reasonless(records, artifacts, tmp):
    cases = [dict(_GOOD_CASE, expected_reason="") for _ in range(5)]
    cases[1]["shape"] = "aliased"
    path = _write_corpus(tmp, cases)
    return records, artifacts, {"corpus_path": path, "corpus": cases}


@mutation("computed_expectations", "probe_expectations_are_literal_not_hardcoded_pass",
          "expectations are derived from the validator instead of stated")
def _m_computed(records, artifacts, tmp):
    path = os.path.join(tmp, "computed_corpus.py")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            "import json\n"
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "import n2_resolve\n"
            "CANONICAL_LIVE_ENTRY = \'Bash\'\n"
            "PAYLOADS = [json.loads(\'%s\')]\n"
            "PROBE_CASES = [{\n"
            "    \'id\': \'c0\', \'shape\': \'direct\', \'note\': \'n\',\n"
            "    \'payload\': p,\n"
            "    \'expected_verdict\': n2_resolve.verdict_of(p),\n"
            "    \'expected_reason\': n2_resolve.reason_of(p),\n"
            "    \'basis\': \'self-referential\',\n"
            "} for p in PAYLOADS]\n"
            "def cases():\n"
            "    return [dict(c) for c in PROBE_CASES]\n"
            % (SRC, json.dumps(_GOOD_CASE["payload"]).replace("\'", "\\\'")))
    cases = load_corpus(path)
    return records, artifacts, {"corpus_path": path, "corpus": cases}


@mutation("thin_corpus", "probe_corpus_covers_each_shape",
          "two probes, one shape: coverage is a lie")
def _m_thin_corpus(records, artifacts, tmp):
    cases = [dict(_GOOD_CASE), dict(_GOOD_CASE, id="c_other")]
    path = _write_corpus(tmp, cases)
    return records, artifacts, {"corpus_path": path, "corpus": cases}


@mutation("wrong_expectation", "probe_verdicts_match_validator",
          "a probe claims the aliased name `bash` passes")
def _m_wrong_expectation(records, artifacts, tmp):
    case = dict(_GOOD_CASE, id="c_wrong", shape="aliased",
                expected_verdict="pass", expected_reason="OK")
    case["payload"] = {"tool_call": {"name": "bash", "input": {"command": "uname -s"},
                                     "child": {"agent": "a", "depth": 1}}}
    path = _write_corpus(tmp, [case])
    return records, artifacts, {"corpus_path": path, "corpus": [case]}


def _blk(rec):
    msg = rec.get("message") if isinstance(rec, dict) else None
    content = msg.get("content") if isinstance(msg, dict) else None
    return content if isinstance(content, list) else []


def self_test():
    """Baseline must be green for each gate; each mutation must turn its gate red."""
    base_records, base_artifacts = _synthetic_bundle()
    problems = []
    print("== n2_check --self-test: can every gate be made red? ==")

    with tempfile.TemporaryDirectory() as tmp:
        transcript_path, artifacts = _write_synthetic(tmp, base_records, base_artifacts)
        bundle = Bundle(base_records, artifacts, transcript_path)
        results = run_gates(bundle, skip=(SELF_GATE,))
        red = [n for n, ok, _ in results if not ok]
        print("baseline synthetic fixture: %d/%d gates green %s" % (
            len(results) - len(red), len(results), ("(red: %s)" % red) if red else ""))
        if red:
            problems.append("the baseline fixture is already red on %s, so a mutation there "
                            "proves nothing -- fix the fixture or the gate" % red)

    for name, (gate, label, mutate) in sorted(MUTATIONS.items()):
        with tempfile.TemporaryDirectory() as tmp:
            records, artifacts, extra = mutate(json.loads(json.dumps(base_records)),
                                               json.loads(json.dumps(base_artifacts)), tmp)
            post_edit = extra.pop("_post_edit_transcript", False)
            transcript_path, artifacts = _write_synthetic(tmp, records, artifacts)
            if post_edit:
                with open(transcript_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"type": "system", "sessionId": "synth",
                                             "injected": "after the capture"}) + "\n")
            bundle = Bundle(records, artifacts, transcript_path, **extra)
            rows = run_gates(bundle, only={gate})
            if rows:
                ok, evidence = rows[0][1], rows[0][2]
            else:
                ok, evidence = None, "gate %r is not in GATES" % gate
        if ok is False:
            mark = "FIRED"
        elif ok is True:
            mark = "NOT-FIRED"
        else:
            mark = "GATE-MISSING"
        print("  %-26s -> %-52s %-12s [%s]" % (name, gate, mark, label))
        if ok is not False:
            problems.append("mutation %r left gate %r at %r (evidence: %s)"
                            % (name, gate, mark, str(evidence)[:160]))
    print()
    print("mutations applied : %d" % len(MUTATIONS))
    print("problems          : %d" % len(problems))
    for problem in problems:
        print("  FAIL  %s" % problem)
    if problems:
        print("\nRESULT: FAILURES PRESENT")
        return 1
    print("\nRESULT: ALL PASS (every gate has a mutation that turns it red)")
    return 0


# --------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description="n2 closed-world gate")
    parser.add_argument("--transcript", default=os.environ.get("N2_TRANSCRIPT"),
                        help="session transcript JSONL (default $N2_TRANSCRIPT, else %s)"
                             % DEFAULT_TRANSCRIPT)
    parser.add_argument("--artifacts", default=DEFAULT_ARTIFACTS)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    opts = parser.parse_args(argv)

    if opts.self_test:
        return self_test()

    transcript_path = opts.transcript or DEFAULT_TRANSCRIPT
    if not os.path.exists(transcript_path):
        print("PRECONDITION no transcript at %s (set N2_TRANSCRIPT or pass --transcript)"
              % transcript_path)
        print("This is not a verdict on the run: nothing was checked.")
        print("RESULT: PRECONDITION UNMET")
        return 2
    if not os.path.exists(opts.artifacts):
        print("PRECONDITION no artifacts at %s -- run the live round trip first "
              "(python3 src/n2_live.py --envelope ... )" % opts.artifacts)
        print("RESULT: PRECONDITION UNMET")
        return 2
    try:
        records, errors = n2_transcript.load_transcript(transcript_path)
        with open(opts.artifacts, "r", encoding="utf-8") as handle:
            artifacts = json.load(handle)
    except ValueError as exc:
        print("PRECONDITION unreadable input: %s" % exc)
        print("RESULT: PRECONDITION UNMET")
        return 2
    if not records:
        print("PRECONDITION transcript %s has %d parseable records" % (transcript_path, len(records)))
        print("RESULT: PRECONDITION UNMET")
        return 2

    bundle = Bundle(records, artifacts, transcript_path, load_corpus())
    results = run_gates(bundle)
    failed = [row for row in results if not row[1]]

    print("== n2 closed-world check ==")
    print("transcript   : %s (%d records, %d unparseable, %d executed calls)" % (
        transcript_path, len(records), len(errors), len(n2_transcript.paired_calls(records))))
    print("artifacts    : %s (%d run(s))" % (opts.artifacts, len(bundle.runs)))
    print("probes       : %d from scripts/malformed_probe_cases.py" % len(bundle.corpus))
    print("canonical    : %s" % n2_canonical.CANONICAL_LIVE_ENTRY)
    print()
    for name, ok, evidence in results:
        line = "  %-5s %-48s %s" % ("PASS" if ok else "FAIL", name,
                                    evidence if (ok and opts.verbose) or not ok else "")
        print(line.rstrip())
    print()
    print("gates run    : %d" % len(results))
    print("gates failed : %d" % len(failed))
    if failed:
        print("\nFailed checks and their evidence:")
        for name, _ok, evidence in failed:
            print("  FAIL  %s\n        %s" % (name, evidence))
        print("\nRESULT: FAILURES PRESENT")
        return 1
    print("\nRESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
