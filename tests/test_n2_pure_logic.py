#!/usr/bin/env python3
"""Pure-logic tests: canonicalization, envelope parsing, wrapper resolution, probes.

These never read the transcript and never claim a live call happened; they pin the
judgement that `checks/n2_check.py` re-runs over the real artifacts.

Run: python3 -m pytest tests/test_n2_pure_logic.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import conftest                                                # noqa: E402 (path setup)
import n2_canonical
import n2_envelope as env
import n2_resolve
import n2_live

REGISTRY = n2_canonical.DEFAULT_REGISTRY
CORPUS = conftest.load_module("malformed_probe_cases_under_test", conftest.CORPUS)
CASES = CORPUS.cases()
BAD_INPUT = "x"


def _payload(name=BAD_INPUT, command="uname -s", child=None, invocation_id="i-1"):
    call = {"name": name, "input": {"command": command}}
    if child is not None:
        call["child"] = child
    if invocation_id is not None:
        call["invocation_id"] = invocation_id
    return {"tool_call": call}


# --------------------------------------------------------------- canonicalization
def test_canonical_entry_is_bash_and_is_the_only_one():
    assert n2_canonical.CANONICAL_LIVE_ENTRY == "Bash"
    assert REGISTRY.classify("Bash") == (n2_canonical.CANONICAL, "Bash")


def test_canonicalize_strips_only_mechanical_noise():
    assert n2_canonical.canonicalize("  Bash\n") == "Bash"
    assert n2_canonical.canonicalize("<｜Bash｜>") == "Bash"
    assert n2_canonical.canonicalize("<|Bash|>") == "Bash"
    assert n2_canonical.canonicalize("Ｂash") == "Bash"      # NFKC full-width form


def test_canonicalize_never_folds_case_and_never_splits_punctuation():
    # Widening these two would turn `bash`, `Bash_1` and `mcp__local__Bash` into the
    # canonical entry, i.e. delete the whole alias class.
    assert n2_canonical.canonicalize("bash") == "bash"
    assert n2_canonical.canonicalize("BASH") == "BASH"
    assert n2_canonical.canonicalize("Bash_1") == "Bash_1"
    assert n2_canonical.canonicalize("mcp__local__Bash") == "mcp__local__Bash"


def test_canonicalize_of_non_strings_is_empty():
    for value in (None, 1, ["Bash"], {"name": "Bash"}, True):
        assert n2_canonical.canonicalize(value) == ""


def test_alias_table_maps_to_live_names_and_holds_no_canonical_key():
    # `Bash` may not appear in the alias table: it would make the canonical spelling
    # classify as an alias.
    assert "Bash" not in n2_canonical.ALIAS_OF
    assert set(n2_canonical.ALIAS_OF.values()) <= (
        set(n2_canonical.DIRECT_LIVE_TOOLS) | set(n2_canonical.MCP_LIVE_TOOLS))
    assert n2_canonical.ALIAS_OF["bash"] == "Bash"


def test_mcp_form_liveness_comes_from_the_read_roster():
    for server in n2_canonical.LIVE_MCP_SERVERS:
        assert REGISTRY.is_live_namespaced("mcp__%s__anything" % server)
    assert not REGISTRY.is_live_namespaced("mcp__agent__create_rollback_snapshot")
    assert not REGISTRY.is_live_namespaced("mcp__local__Bash")
    assert not REGISTRY.is_live_namespaced("Bash")


def test_wrapper_reach_of_the_live_wrapper_excludes_the_canonical_entry():
    resolves, inner = REGISTRY.wrapper_resolves("mcp_call", "Bash")
    assert resolves is False and inner == "Bash"
    # The same call reaching an MCP-namespaced tool is live -- but still not canonical.
    resolves, _inner = REGISTRY.wrapper_resolves("mcp_call", "mcp__node-repl__node_repl")
    assert resolves is False


# ------------------------------------------------------------------- envelope
def test_envelope_requires_the_tool_call_key():
    for payload in ({}, {"command": "uname -s"}, "run uname", None, [], 0):
        parsed = env.parse_envelope(payload, canonicalize=n2_canonical.canonicalize)
        assert isinstance(parsed, env.EnvelopeError), payload
        assert parsed.reason == env.E_NO_ENVELOPE


def test_near_miss_envelope_key_is_not_rescued_by_fuzzy_matching():
    for key in ("toolcall", "toolCall", "tool-call", "calls", "tool_calls"):
        parsed = env.parse_envelope({key: {"name": "Bash", "input": {"command": "x"}}},
                                    canonicalize=n2_canonical.canonicalize)
        assert isinstance(parsed, env.EnvelopeError)
        assert parsed.reason == env.E_NO_ENVELOPE, key


@pytest.mark.parametrize("tool_call", [
    "Bash",
    ["Bash"],
    None,
    {},                                       # name and input missing
    {"name": "Bash"},                         # input missing
    {"input": {"command": "x"}},              # name missing
    {"name": ["Bash"], "input": {}},          # name not a string
    {"name": "   ", "input": {"command": "x"}},        # blank after canonicalization
    {"name": "Bash", "input": "{\"command\": \"x\"}"},  # double-encoded input
    {"name": "Bash", "cmd": "x"},             # unknown key inside the envelope
    {"name": "Bash", "input": {}, "child": "first"},   # child not an object
    {"name": "Bash", "input": {}, "child": {"depth": "1"}},
    {"name": "Bash", "input": {}, "child": {"depth": 0}},
    {"name": "Bash", "input": {}, "child": {"agent": "a", "depth": 1, "tool": "Bash"}},
])
def test_malformed_envelopes_are_refused_as_shape_defects(tool_call):
    parsed = env.parse_envelope({"tool_call": tool_call},
                                canonicalize=n2_canonical.canonicalize)
    assert isinstance(parsed, env.EnvelopeError), tool_call
    assert parsed.reason == env.E_MALFORMED_ENVELOPE, parsed.detail


def test_keys_beside_the_envelope_are_allowed_because_children_send_prose():
    parsed = env.parse_envelope({"thought": "let me run this", "note": ["x"],
                                 "tool_call": {"name": "Bash",
                                               "input": {"command": "uname -s"}}},
                                canonicalize=n2_canonical.canonicalize)
    assert isinstance(parsed, env.Envelope), parsed.detail
    assert parsed.name == "Bash"
    assert parsed.input == {"command": "uname -s"}


def test_valid_envelope_exposes_the_children_nestedness_marker():
    parsed = env.parse_envelope(_payload(name="Bash", child={"agent": "kid", "depth": 2}),
                                canonicalize=n2_canonical.canonicalize)
    assert isinstance(parsed, env.Envelope)
    assert parsed.nested_depth == 2
    assert parsed.invocation_id == "i-1"


def test_envelope_without_a_child_marker_has_no_depth():
    parsed = env.parse_envelope({"tool_call": {"name": "Bash", "input": {"command": "x"}}},
                                canonicalize=n2_canonical.canonicalize)
    assert isinstance(parsed, env.Envelope)
    assert parsed.nested_depth is None


# ------------------------------------------------------------------- resolver
@pytest.mark.parametrize("name,cls", [
    ("Bash", n2_canonical.CANONICAL),
    ("<｜Bash｜>", n2_canonical.CANONICAL),
    ("bash", n2_canonical.ALIAS),
    ("BASH", n2_canonical.ALIAS),
    ("shell", n2_canonical.ALIAS),
    ("mcp__local__Bash", n2_canonical.ALIAS),
    ("Read", n2_canonical.LIVE),
    ("mcp__node-repl__node_repl", n2_canonical.LIVE),
    ("mcp__agent__create_rollback_snapshot", n2_canonical.ABSENT),
    ("<｜call_xxx｜>", n2_canonical.OPAQUE),
    ("mcp_call", n2_canonical.WRAPPER),
    ("Tool", n2_canonical.WRAPPER_DEAD),
])
def test_registry_classification(name, cls):
    assert REGISTRY.classify(name)[0] == cls, REGISTRY.classify(name)


def test_direct_canonical_nested_call_passes():
    decision = n2_resolve.evaluate(_payload(name="Bash", command="uname -s",
                                            child={"agent": "kid", "depth": 1}))
    assert decision.verdict == "pass" and decision.reason == n2_resolve.OK
    assert decision.diagnostics["canonical_name"] == "Bash"
    assert decision.diagnostics["entry_class"] == n2_canonical.CANONICAL


@pytest.mark.parametrize("name,reason", [
    ("bash", n2_resolve.E_ALIAS),
    ("BASH", n2_resolve.E_ALIAS),
    ("mcp__local__Bash", n2_resolve.E_ALIAS),
    ("Read", n2_resolve.E_LIVE_NOT_CANONICAL),
    ("mcp__node-repl__node_repl", n2_resolve.E_LIVE_NOT_CANONICAL),
    ("mcp__agent__create_rollback_snapshot", n2_resolve.E_FABRICATED_NAME),
    ("Agent", n2_resolve.E_FABRICATED_NAME),
    ("<｜call_xxx｜>", n2_resolve.E_OPAQUE_CALL_ID),
])
def test_aliased_and_fabricated_live_names_fail(name, reason):
    decision = n2_resolve.evaluate(_payload(name=name, child={"agent": "kid", "depth": 1}))
    assert decision.verdict == "fail", decision.evidence()
    assert decision.reason == reason, decision.evidence()


def test_wrapper_fails_even_when_its_inner_call_names_the_live_tool():
    payload = {"tool_call": {"name": "mcp_call",
                             "input": {"toolName": "Bash",
                                       "arguments": {"command": "uname -s"}},
                             "child": {"agent": "kid", "depth": 1}}}
    decision = n2_resolve.evaluate(payload)
    assert decision.verdict == "fail"
    assert decision.reason == n2_resolve.E_WRAPPER_TARGET_NOT_CANONICAL
    # evidence must name both sides, or a reader cannot audit the refusal
    assert "Bash" in decision.evidence() and "mcp_call" in decision.evidence()


def test_wrapper_target_that_is_live_nowhere_reports_a_distinct_reason():
    payload = {"tool_call": {"name": "mcp_call",
                             "input": {"toolName": "mcp__agent__restore_to_snapshot",
                                       "arguments": {}},
                             "child": {"agent": "kid", "depth": 1}}}
    assert n2_resolve.reason_of(payload) == n2_resolve.E_WRAPPER_TARGET_ABSENT


def test_wrapper_that_is_not_live_at_all_reports_that_first():
    for wrapper in sorted(n2_canonical.DEAD_WRAPPERS):
        payload = {"tool_call": {"name": wrapper,
                                 "input": {"toolName": "Bash", "arguments": {}},
                                 "child": {"agent": "kid", "depth": 1}}}
        assert n2_resolve.reason_of(payload) == n2_resolve.E_WRAPPER_NOT_LIVE, wrapper


def test_wrapper_missing_its_inner_name_is_a_shape_defect():
    payload = {"tool_call": {"name": "mcp_call", "input": {"arguments": {}},
                             "child": {"agent": "kid", "depth": 1}}}
    assert n2_resolve.reason_of(payload) == n2_resolve.E_MALFORMED_ENVELOPE


def test_canonical_entry_with_input_that_cannot_be_dispatched_fails():
    # Bash's declared input requires `command`; a canonical name with the wrong input
    # must not be waved through just because the name resolved.
    payload = {"tool_call": {"name": "Bash", "input": {"cmdline": "uname -s"},
                             "child": {"agent": "kid", "depth": 1}}}
    assert n2_resolve.reason_of(payload) == n2_resolve.E_MALFORMED_ENVELOPE


def test_registry_is_a_parameter_so_the_positive_wrapper_branch_is_reachable():
    """The alternative spec's `Tool -> Bash` shape must be *testable*, not dead code.

    With a registry that exposes a live dispatcher over flat names, a wrapper resolves
    onto the canonical entry and passes.  On THIS host's registry it fails, so the
    refusal is a property of the host, not of a broken resolver.
    """
    flat_dispatcher = n2_canonical.Registry(
        live_wrappers=frozenset({"Tool"}), wrapper_reach={"Tool": ""},
        dead_wrappers=frozenset())
    payload = {"tool_call": {"name": "Tool",
                             "input": {"toolName": "Bash",
                                       "arguments": {"command": "uname -s"}},
                             "child": {"agent": "kid", "depth": 1}}}
    with_injected = n2_resolve.evaluate(payload, registry=flat_dispatcher)
    assert with_injected.verdict == "pass", with_injected.evidence()
    assert with_injected.reason == n2_resolve.OK
    on_this_host = n2_resolve.evaluate(payload)
    assert on_this_host.verdict == "fail"
    assert on_this_host.reason == n2_resolve.E_WRAPPER_NOT_LIVE


def test_positive_branch_also_needs_a_live_wrapper_not_just_reach():
    dead_dispatcher = n2_canonical.Registry(
        live_wrappers=frozenset(), wrapper_reach={},
        dead_wrappers=frozenset({"Tool"}))
    payload = {"tool_call": {"name": "Tool",
                             "input": {"toolName": "Bash", "arguments": {}},
                             "child": {"agent": "kid", "depth": 1}}}
    assert n2_resolve.reason_of(payload, registry=dead_dispatcher) == \
        n2_resolve.E_WRAPPER_NOT_LIVE


def test_evaluate_never_raises_on_junk_payloads():
    for payload in (None, 0, [], {}, "", {"tool_call": None},
                    {"tool_call": {"name": 1, "input": 2}},
                    {"tool_call": {"name": "Bash", "input": None}}):
        decision = n2_resolve.evaluate(payload)
        assert decision.verdict in ("pass", "fail")
        assert decision.reason in n2_resolve.REASONS


# --------------------------------------------------------------------- probes
def test_probe_corpus_covers_every_required_shape():
    shapes = {case["shape"] for case in CASES}
    for shape in ("direct", "aliased", "wrapped", "fabricated", "no_envelope",
                  "malformed_envelope"):
        assert shape in shapes, shape
    assert len(CASES) >= 4


def test_every_probe_states_a_verdict_and_a_reason():
    for case in CASES:
        assert case["expected_verdict"] in ("pass", "fail"), case["id"]
        assert case["expected_reason"], "%s has no reason" % case["id"]
        assert case["expected_reason"] in n2_resolve.REASONS, case["id"]
        assert case["basis"], case["id"]
        assert case["payload"] is not None, case["id"]


def test_probe_expectations_are_mixed_neither_all_pass_nor_all_fail():
    verdicts = [case["expected_verdict"] for case in CASES]
    assert "pass" in verdicts and "fail" in verdicts
    reasons = {case["expected_reason"] for case in CASES}
    assert len(reasons) >= 4, reasons
    assert sum(1 for v in verdicts if v == "pass") < len(verdicts)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_validator_matches_the_predeclared_expectation(case):
    """The corpus was written before the implementation; the code answers to it."""
    decision = n2_resolve.evaluate(case["payload"])
    assert decision.verdict == case["expected_verdict"], decision.evidence()
    assert decision.reason == case["expected_reason"], decision.evidence()


def test_direct_live_call_passes_while_alias_wrapper_fabricated_all_fail():
    for case in CASES:
        verdict = n2_resolve.verdict_of(case["payload"])
        if case["shape"] == "direct":
            assert verdict == "pass", case["id"]
        elif case["shape"] in ("aliased", "wrapped", "fabricated"):
            assert verdict == "fail", case["id"]


def test_corpus_cli_exit_codes_are_distinct():
    import subprocess
    good = subprocess.run([sys.executable, conftest.CORPUS, "--self-test"],
                          capture_output=True, text=True, cwd=conftest.ROOT)
    assert good.returncode == 0, good.stdout
    verified = subprocess.run([sys.executable, conftest.CORPUS, "--verify"],
                              capture_output=True, text=True, cwd=conftest.ROOT)
    assert verified.returncode == 0, verified.stdout
    # a typo'd flag must not fall through to the default table and exit 0
    typo = subprocess.run([sys.executable, conftest.CORPUS, "--verfy"],
                          capture_output=True, text=True, cwd=conftest.ROOT)
    assert typo.returncode == 2, typo.stdout + typo.stderr
    assert "PRECONDITION" in typo.stdout


def test_corpus_self_test_sheds_a_mutation(install_corpus_mutation, tmp_path):
    """A self-test that can never go red is not a self-test."""
    import subprocess
    for name, expect_rc in (("drop_a_required_shape", 1), ("single_verdict_corpus", 1),
                            ("unreasoned_case", 1), ("duplicate_ids", 1)):
        path = install_corpus_mutation(name)
        proc = subprocess.run([sys.executable, path, "--self-test"],
                              capture_output=True, text=True, cwd=conftest.ROOT)
        assert proc.returncode == expect_rc, (name, proc.returncode, proc.stdout[-400:])
        assert "FAIL" in proc.stdout, name


# ------------------------------------------------------------- harness safety
def _parse(payload):
    parsed = env.parse_envelope(payload, canonicalize=n2_canonical.canonicalize)
    assert isinstance(parsed, env.Envelope), parsed.detail
    return parsed


def test_live_harness_dispatches_only_a_judged_envelope(tmp_path):
    """A refused nested payload must never reach the command line."""
    refused = _parse({"tool_call": {"name": "bash",
                                    "input": {"command": "touch %s" % (tmp_path / "ran")},
                                    "child": {"agent": "kid", "depth": 1}}})
    with pytest.raises(n2_live.NotDispatched) as excinfo:
        n2_live.dispatch_nested(refused, nonce="deadbeef", cwd=str(tmp_path))
    assert "E_ALIAS" in str(excinfo.value)
    assert not (tmp_path / "ran").exists()


def test_live_harness_runs_a_judged_envelope(tmp_path):
    """The paired positive: the same call with the canonical name does dispatch."""
    ok = _parse({"tool_call": {"name": "Bash",
                               "input": {"command": "python3 %s" % conftest.NESTED_CHILD},
                               "child": {"agent": "kid", "depth": 1}}})
    report, rc = n2_live.dispatch_nested(ok, nonce="cafebabe", cwd=conftest.ROOT)
    assert rc == 0, report
    assert report["returncode"] == 0
    assert report["observed"]["ppid"] == os.getpid()
    assert report["observed"]["nonce"] == "cafebabe"
    assert report["observed"]["secret"].startswith("sec-")


def test_wrapper_envelope_is_refused_before_dispatch():
    """The guard must not be reachable by name alone: mcp_call wrapping Bash is refused."""
    wrapped = {"tool_call": {"name": "mcp_call",
                             "input": {"command": "touch should-not-exist",
                                       "toolName": "Bash", "arguments": {}},
                             "child": {"agent": "kid", "depth": 1}}}
    parsed = env.parse_envelope(wrapped, canonicalize=n2_canonical.canonicalize)
    assert isinstance(parsed, env.Envelope)          # shape is fine...
    decision = n2_resolve.evaluate(wrapped)
    assert decision.verdict == "fail"                 # ...the judgement is what stops it


def test_nested_child_secret_differs_per_run():
    import subprocess
    seen = set()
    for _ in range(2):
        proc = subprocess.run([sys.executable, conftest.NESTED_CHILD],
                              capture_output=True, text=True, cwd=conftest.ROOT)
        seen.add(proc.stdout.strip())
    assert len(seen) == 2, "child stdout did not vary: the secret is not fresh"
