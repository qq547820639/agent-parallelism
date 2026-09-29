#!/usr/bin/env python3
"""Malformed-probe corpus for the n2 closed-world variant (written BEFORE any implementation).

RUN THIS SCRIPT DIRECTLY; do not read it into your context.
Stdlib only. No network. Read-only. Exit codes are three-valued (see `--verify`).

    python3 scripts/malformed_probe_cases.py            # human table
    python3 scripts/malformed_probe_cases.py --json     # machine-readable corpus
    python3 scripts/malformed_probe_cases.py --verify   # run src/n2_resolve.py against it
    python3 scripts/malformed_probe_cases.py --self-test # corpus is internally sound

Every case carries `expected_verdict` ("pass" | "fail") AND `expected_reason`.
A bare "PASS" is not an expectation: the reason code is the assertion, and a case
that expects "pass" must state WHY it is legal, so a validator that rejects
everything cannot score 100% on this corpus.

-------------------------------------------------------------------------------
Step 1 of the brief, done before writing implementation: what is the canonical
live tool-call entry in THIS host?
-------------------------------------------------------------------------------

Discovered from the host's own tool listing (this session), not from the sibling
spec's table in README.md (that table names `mcp__agent__create_rollback_snapshot`,
`restore_to_snapshot`, `Agent`, `Task`, `mcp_get` -- names that belong to another
closed world and are NOT present here):

  * The host declares a flat, directly-addressable live tool set for command
    execution: `Bash` ("Executes a given bash command"). Its declared input shape
    is `{command, description, dir_path, run_in_background, timeout}` -- `command`
    is required. Other live flat names seen in the same listing: Read, Write, Edit,
    Glob, Grep, WebFetch, WebSearch, NotebookEdit, Skill, TaskCreate, TaskGet,
    TaskList, TaskStop, TaskUpdate, ImageGen, SearchKnowledge, mcp_get, mcp_list,
    mcp_call.
  * Namespaced forms `mcp__<server>__<tool>` are live only for servers this
    session actually has. Read by a real call, `mcp_list` returned 139 tools over
    servers {plugin_chrome-devtools-mcp_chrome-devtools, qca, builtin,
    extension-market, browser-use, node-repl}. There is NO `mcp__agent__*` server,
    so the README's rollback/restore names are fabricated *in this closed world*.
  * `mcp_call` is live but it is a *wrapper*: its own declared contract is
    "Invoke a tool by its fully-qualified name" `mcp__<server>__<tool>`, so it can
    only ever route into the MCP namespace. It cannot carry a shell command, so it
    cannot be the canonical live entry for a nested command-execution payload --
    which is exactly the shape this brief wraps. This is the host-doc reason the
    brief's alternative example ("`mcp_call` for Bash-like hosts") is rejected here.

DECISION: canonical live tool-call entry = `Bash`, reached by its exact registered
spelling (or that spelling inside the host's `<｜…｜>` call-marker rendering).
Confirmation that the entry is really live is NOT taken from this file: the pure
registry in `src/n2_canonical.py` only marks a name live-by-receipt, and
`checks/n2_check.py` re-derives the receipt from real tool_use/tool_result pairs in
the transcript. This corpus asserts expectations only.

-------------------------------------------------------------------------------
Envelope contract (the shape a nested child must emit; src conforms to THIS)
-------------------------------------------------------------------------------

    {"tool_call": {                     # required key; must be an object
        "name": "<str>",                # required; non-empty after canonicalization
        "input": { ... },               # required; must be an object
        "invocation_id": "<str>",       # optional; must be a str if present
        "child": {"agent": "<str>",     # optional; if present: object, depth int >= 1
                  "depth": <int>}}}

No other key may appear inside `tool_call` (strict: `cmd`, `toolName`, `arguments`
at this level are shape defects, not "unknown extras we tolerate"). For a wrapper
entry, the inner call rides inside `input` (`input.toolName` / `input.arguments`).
Extra keys *beside* `tool_call` are allowed -- a child payload legitimately carries
prose alongside its declarative block.

Canonicalization (mechanical wire noise only; it never edits letters or case):
NFKC, strip surrounding whitespace, strip one enclosing `<｜`…`｜>` marker pair.
After that the name must match a registered name EXACTLY.

Reason codes used by the expectations below:

    OK                              legal, dispatchable
    E_NO_ENVELOPE                   payload carries no `tool_call` envelope
    E_MALFORMED_ENVELOPE            envelope present but structurally invalid
    E_ALIAS                         canonicalises to a non-canonical spelling of a
                                    live entry (case variant / invented namespace)
    E_LIVE_NOT_CANONICAL            a live entry that is not the canonical one
    E_FABRICATED_NAME               name is in no registry this session read
    E_OPAQUE_CALL_ID                marker form carried a call id, no name at all
    E_WRAPPER_NOT_LIVE              payload is a wrapper whose own name is not live
    E_WRAPPER_TARGET_NOT_CANONICAL  live wrapper, inner target live but not the
                                    canonical live entry
    E_WRAPPER_TARGET_ABSENT         live wrapper, inner target not live at all

Required coverage (brief): one probe per shape for direct, aliased, wrapped,
fabricated, plus nested payloads with no envelope and with a malformed envelope.
`--self-test` below asserts that coverage from the corpus itself.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

CANONICAL_LIVE_ENTRY = "Bash"

REASON_CODES = (
    "OK",
    "E_NO_ENVELOPE",
    "E_MALFORMED_ENVELOPE",
    "E_ALIAS",
    "E_LIVE_NOT_CANONICAL",
    "E_FABRICATED_NAME",
    "E_OPAQUE_CALL_ID",
    "E_WRAPPER_NOT_LIVE",
    "E_WRAPPER_TARGET_NOT_CANONICAL",
    "E_WRAPPER_TARGET_ABSENT",
)

SHAPES = ("direct", "aliased", "wrapped", "fabricated", "no_envelope", "malformed_envelope",
          "live_not_canonical")


def _child(depth=1):
    return {"agent": "n2-bench-child", "depth": depth}


# --------------------------------------------------------------------------
# The corpus. Expectations are literal data, derived from the brief clauses
# quoted in each case's `basis` -- never computed from the implementation.
# --------------------------------------------------------------------------
PROBE_CASES = [
    # ---- direct: the payload the brief wants to work -----------------------
    {
        "id": "n2p_direct_canonical_bash",
        "shape": "direct",
        "note": "nested declarative tool_call wrapped in the canonical live entry",
        "payload": {
            "tool_call": {
                "name": "Bash",
                "input": {"command": "uname -s", "description": "nested probe"},
                "invocation_id": "n2p-1",
                "child": _child(),
            }
        },
        "expected_verdict": "pass",
        "expected_reason": "OK",
        "basis": "brief: 'A direct live tool call must PASS'; wrapper == canonical live entry.",
    },
    {
        "id": "n2p_direct_marker_form",
        "shape": "direct",
        "note": "canonical name inside the host's <｜…｜> call marker",
        "payload": {
            "tool_call": {
                "name": "<｜Bash｜>",
                "input": {"command": "uname -s", "description": "nested probe"},
                "child": _child(),
            }
        },
        "expected_verdict": "pass",
        "expected_reason": "OK",
        "basis": "brief names the marker rendering as an entry form; canonicalization must "
                 "resolve it onto the live entry, otherwise the rule is untestable in its "
                 "positive branch.",
    },
    {
        "id": "n2p_direct_wrapper_live_and_inner_canonical",
        "shape": "direct",
        "note": "positive branch of the resolver, on an injected registry (see tests); "
                "in THIS host's registry no wrapper is live, so every wrapper case below fails",
        "payload": {
            "tool_call": {
                "name": "Bash",
                "input": {"command": "printf ok\n", "description": "canonical entry, no wrapper"},
                "child": _child(2),
            }
        },
        "expected_verdict": "pass",
        "expected_reason": "OK",
        "basis": "brief: 'all wrappers resolve to that entry after canonicalization' -- a "
                 "payload that already IS the canonical entry resolves trivially.",
    },

    # ---- aliased: an alias of a live name must FAIL ------------------------
    {
        "id": "n2p_alias_lowercase",
        "shape": "aliased",
        "note": "case variant of the canonical entry",
        "payload": {
            "tool_call": {"name": "bash", "input": {"command": "uname -s"}, "child": _child()}
        },
        "expected_verdict": "fail",
        "expected_reason": "E_ALIAS",
        "basis": "brief: 'Aliased ... live names must FAIL'. Canonicalization is mechanical "
                 "noise only; it does not fold case.",
    },
    {
        "id": "n2p_alias_upper",
        "shape": "aliased",
        "note": "upper-case variant of the canonical entry",
        "payload": {
            "tool_call": {"name": "BASH", "input": {"command": "uname -s"}, "child": _child()}
        },
        "expected_verdict": "fail",
        "expected_reason": "E_ALIAS",
        "basis": "same clause; case-only difference from the registered spelling.",
    },
    {
        "id": "n2p_alias_invented_namespace",
        "shape": "aliased",
        "note": "a live flat name wrapped in an mcp__ namespace that has no such server",
        "payload": {
            "tool_call": {
                "name": "mcp__local__Bash",
                "input": {"command": "uname -s"},
                "child": _child(),
            }
        },
        "expected_verdict": "fail",
        "expected_reason": "E_ALIAS",
        "basis": "brief's alias class: mcp_list (real call) shows no `local` server, while the "
                 "last segment is a live flat name -- an alias of the canonical entry, not a "
                 "live namespaced tool.",
    },
    {
        "id": "n2p_alias_verbal",
        "shape": "aliased",
        "note": "host-independent verbal alias for a shell entry",
        "payload": {
            "tool_call": {"name": "shell", "input": {"command": "uname -s"}, "child": _child()}
        },
        "expected_verdict": "fail",
        "expected_reason": "E_ALIAS",
        "basis": "documented in src/n2_canonical.py's alias table as a synonym for the "
                 "command-execution entry; a synonym is not the registered name.",
    },

    # ---- live but not canonical: the closed world still refuses it ---------
    {
        "id": "n2p_live_but_not_canonical",
        "shape": "live_not_canonical",
        "note": "another live flat name, not the canonical entry",
        "payload": {
            "tool_call": {
                "name": "Read",
                "input": {"file_path": "/etc/hosts"},
                "child": _child(),
            }
        },
        "expected_verdict": "fail",
        "expected_reason": "E_LIVE_NOT_CANONICAL",
        "basis": "brief: the nested call 'must be wrapped in a canonical live tool-call entry'. "
                 "Read is live in this host's listing, so this is not a fabricated name and not "
                 "an alias -- the closed world still refuses a non-canonical live entry.",
    },
    # ---- wrapped: a wrapper that does not resolve to the entry must FAIL ---
    {
        "id": "n2p_wrapped_mcp_call_inner_canonical_name",
        "shape": "wrapped",
        "note": "live wrapper mcp_call, inner names the live tool Bash",
        "payload": {
            "tool_call": {
                "name": "mcp_call",
                "input": {
                    "toolName": "Bash",
                    "arguments": {"command": "uname -s"},
                },
                "child": _child(),
            }
        },
        "expected_verdict": "fail",
        "expected_reason": "E_WRAPPER_TARGET_NOT_CANONICAL",
        "basis": "brief: 'A wrapper that does not resolve there must FAIL, even if its inner "
                 "call names a live tool.' mcp_call's own declared contract is a fully "
                 "qualified mcp__server__tool name, so it can never resolve onto Bash.",
    },
    {
        "id": "n2p_wrapped_mcp_call_inner_live_mcp_tool",
        "shape": "wrapped",
        "note": "live wrapper, inner target is genuinely live but is not the canonical entry",
        "payload": {
            "tool_call": {
                "name": "mcp_call",
                "input": {
                    "toolName": "mcp__node-repl__node_repl",
                    "arguments": {"code": "1+1"},
                },
                "child": _child(),
            }
        },
        "expected_verdict": "fail",
        "expected_reason": "E_WRAPPER_TARGET_NOT_CANONICAL",
        "basis": "closed world: only the canonical live entry may wrap a nested call; "
                 "mcp__node-repl__node_repl is live (mcp_list reading) but is not Bash.",
    },
    {
        "id": "n2p_wrapped_mcp_call_inner_absent",
        "shape": "wrapped",
        "note": "live wrapper, inner target absent from every registry this session read",
        "payload": {
            "tool_call": {
                "name": "mcp_call",
                "input": {
                    "toolName": "mcp__agent__restore_to_snapshot",
                    "arguments": {"snapshot": "s1"},
                },
                "child": _child(),
            }
        },
        "expected_verdict": "fail",
        "expected_reason": "E_WRAPPER_TARGET_ABSENT",
        "basis": "README.md's sibling-spec table name; mcp_list returned no mcp__agent__ tool, "
                 "so the wrapper resolves nowhere, let alone to the canonical entry.",
    },
    {
        "id": "n2p_wrapped_tool_proxy_not_live",
        "shape": "wrapped",
        "note": "the other spec's `Tool -> Bash` wrapper example, on this host",
        "payload": {
            "tool_call": {
                "name": "Tool",
                "input": {"toolName": "Bash", "arguments": {"command": "uname -s"}},
                "child": _child(),
            }
        },
        "expected_verdict": "fail",
        "expected_reason": "E_WRAPPER_NOT_LIVE",
        "basis": "brief: wrappers that do not resolve to the canonical live entry FAIL. `Tool` "
                 "appears in no host listing this session read, so the wrapper itself is not a "
                 "live entry even though the inner call names one.",
    },
    {
        "id": "n2p_wrapped_run_tool_not_live",
        "shape": "wrapped",
        "note": "invented dispatcher name wrapping the canonical entry",
        "payload": {
            "tool_call": {
                "name": "run_tool",
                "input": {"toolName": "Bash", "arguments": {"command": "uname -s"}},
                "child": _child(),
            }
        },
        "expected_verdict": "fail",
        "expected_reason": "E_WRAPPER_NOT_LIVE",
        "basis": "same clause; a generic dispatcher shape is exactly the wrapper class the "
                 "brief wants refused.",
    },

    # ---- fabricated: a name no live entry ever had -------------------------
    {
        "id": "n2p_fabricated_rollback_snapshot",
        "shape": "fabricated",
        "note": "MCP-form name quoted from README.md but not present in this closed world",
        "payload": {
            "tool_call": {
                "name": "mcp__agent__create_rollback_snapshot",
                "input": {"label": "before-write"},
                "child": _child(),
            }
        },
        "expected_verdict": "fail",
        "expected_reason": "E_FABRICATED_NAME",
        "basis": "brief: 'fabricated live names must FAIL'. mcp_list (real call, 139 tools) has "
                 "no mcp__agent__ server.",
    },
    {
        "id": "n2p_fabricated_opaque_call_id",
        "shape": "fabricated",
        "note": "marker form carrying only a call id, no name",
        "payload": {
            "tool_call": {
                "name": "<｜call_xxx｜>",
                "input": {"command": "uname -s"},
                "child": _child(),
            }
        },
        "expected_verdict": "fail",
        "expected_reason": "E_OPAQUE_CALL_ID",
        "basis": "the brief lists <｜call_xxx｜> as an entry FORM; a form is not a name. "
                 "Canonicalising it yields `call_xxx`, which resolves to no registered entry.",
    },
    {
        "id": "n2p_fabricated_agent_dispatch",
        "shape": "fabricated",
        "note": "dispatch name from the sibling spec's registry; absent in this session",
        "payload": {
            "tool_call": {
                "name": "Agent",
                "input": {"prompt": "spawn a child"},
                "child": _child(),
            }
        },
        "expected_verdict": "fail",
        "expected_reason": "E_FABRICATED_NAME",
        "basis": "closed world: a child may not spawn grandchildren. `Agent`/`Task` are in "
                 "README.md's table but not in this session's tool listing.",
    },

    # ---- envelopeless nested payloads -------------------------------------
    {
        "id": "n2p_no_envelope_bare_command",
        "shape": "no_envelope",
        "note": "nested payload that just carries the command",
        "payload": {"command": "uname -s", "child": _child()},
        "expected_verdict": "fail",
        "expected_reason": "E_NO_ENVELOPE",
        "basis": "brief: 'an envelopeless nested payload must FAIL'.",
    },
    {
        "id": "n2p_no_envelope_empty",
        "shape": "no_envelope",
        "note": "empty payload object",
        "payload": {},
        "expected_verdict": "fail",
        "expected_reason": "E_NO_ENVELOPE",
        "basis": "same clause; nothing to resolve.",
    },
    {
        "id": "n2p_no_envelope_prose_only",
        "shape": "no_envelope",
        "note": "child answered in prose, no declarative block",
        "payload": "please run uname -s for me",
        "expected_verdict": "fail",
        "expected_reason": "E_NO_ENVELOPE",
        "basis": "same clause; a non-object payload cannot contain an envelope.",
    },
    {
        "id": "n2p_no_envelope_wrong_key_name",
        "shape": "no_envelope",
        "note": "a declarative block under a different key is not the envelope",
        "payload": {"toolcall": {"name": "Bash", "input": {"command": "uname -s"}}},
        "expected_verdict": "fail",
        "expected_reason": "E_NO_ENVELOPE",
        "basis": "same clause; the key is part of the contract, and near-miss keys must not be "
                 "rescued by fuzzy matching.",
    },

    # ---- malformed envelopes ----------------------------------------------
    {
        "id": "n2p_malformed_tool_call_is_string",
        "shape": "malformed_envelope",
        "note": "tool_call is a bare string",
        "payload": {"tool_call": "Bash"},
        "expected_verdict": "fail",
        "expected_reason": "E_MALFORMED_ENVELOPE",
        "basis": "brief: 'nested payloads with a malformed tool_call envelope' must FAIL.",
    },
    {
        "id": "n2p_malformed_missing_input",
        "shape": "malformed_envelope",
        "note": "envelope names the canonical entry but has no input object",
        "payload": {"tool_call": {"name": "Bash", "child": _child()}},
        "expected_verdict": "fail",
        "expected_reason": "E_MALFORMED_ENVELOPE",
        "basis": "same clause -- and note this is the case a name-only validator would wave "
                 "through: the name is canonical, the shape is still broken.",
    },
    {
        "id": "n2p_malformed_missing_name",
        "shape": "malformed_envelope",
        "note": "input present, name absent",
        "payload": {
            "tool_call": {"input": {"command": "uname -s"}, "invocation_id": "n2p-x", "child": _child()}
        },
        "expected_verdict": "fail",
        "expected_reason": "E_MALFORMED_ENVELOPE",
        "basis": "nothing to resolve, so resolution must refuse rather than guess.",
    },
    {
        "id": "n2p_malformed_name_not_string",
        "shape": "malformed_envelope",
        "note": "name is a list",
        "payload": {
            "tool_call": {"name": ["Bash"], "input": {"command": "uname -s"}, "child": _child()}
        },
        "expected_verdict": "fail",
        "expected_reason": "E_MALFORMED_ENVELOPE",
        "basis": "same clause; a list is not a name.",
    },
    {
        "id": "n2p_malformed_name_blank_after_canonicalize",
        "shape": "malformed_envelope",
        "note": "name is whitespace only",
        "payload": {
            "tool_call": {"name": "   ", "input": {"command": "uname -s"}, "child": _child()}
        },
        "expected_verdict": "fail",
        "expected_reason": "E_MALFORMED_ENVELOPE",
        "basis": "a name field that canonicalises to nothing is a shape defect, not a wrong name.",
    },
    {
        "id": "n2p_malformed_input_is_json_string",
        "shape": "malformed_envelope",
        "note": "input double-encoded as a JSON string",
        "payload": {
            "tool_call": {
                "name": "Bash",
                "input": "{\"command\": \"uname -s\"}",
                "child": _child(),
            }
        },
        "expected_verdict": "fail",
        "expected_reason": "E_MALFORMED_ENVELOPE",
        "basis": "same clause; the closed world does not re-parse strings into objects to be "
                 "helpful.",
    },
    {
        "id": "n2p_malformed_unknown_key_in_envelope",
        "shape": "malformed_envelope",
        "note": "cmd instead of input, at envelope level",
        "payload": {
            "tool_call": {"name": "Bash", "cmd": "uname -s", "child": _child()}
        },
        "expected_verdict": "fail",
        "expected_reason": "E_MALFORMED_ENVELOPE",
        "basis": "envelope contract above forbids extra keys inside tool_call.",
    },
    {
        "id": "n2p_malformed_child_depth_type",
        "shape": "malformed_envelope",
        "note": "child.depth is a string",
        "payload": {
            "tool_call": {
                "name": "Bash",
                "input": {"command": "uname -s"},
                "child": {"agent": "n2-bench-child", "depth": "1"},
            }
        },
        "expected_verdict": "fail",
        "expected_reason": "E_MALFORMED_ENVELOPE",
        "basis": "the nestedness marker must itself be well-formed or the check cannot tell a "
                 "grandchild from a lie.",
    },
    {
        "id": "n2p_malformed_wrapper_missing_inner_name",
        "shape": "malformed_envelope",
        "note": "live wrapper whose input carries no inner toolName",
        "payload": {
            "tool_call": {
                "name": "mcp_call",
                "input": {"arguments": {"command": "uname -s"}},
                "child": _child(),
            }
        },
        "expected_verdict": "fail",
        "expected_reason": "E_MALFORMED_ENVELOPE",
        "basis": "a wrapper with nothing inside it resolves nowhere; reporting the shape defect "
                 "beats reporting a wrapper-target code the reader would misread.",
    },
    {
        "id": "n2p_malformed_canonical_input_schema",
        "shape": "malformed_envelope",
        "note": "canonical entry, but input lacks the entry's own required `command`",
        "payload": {
            "tool_call": {
                "name": "Bash",
                "input": {"cmdline": "uname -s", "description": "nested probe"},
                "child": _child(),
            }
        },
        "expected_verdict": "fail",
        "expected_reason": "E_MALFORMED_ENVELOPE",
        "basis": "host doc: Bash's declared input object requires `command` (string). Wrapping a "
                 "canonical name is not enough if the payload cannot be dispatched.",
    },
]

REQUIRED_SHAPES = ("direct", "aliased", "wrapped", "fabricated", "no_envelope", "malformed_envelope")


def cases():
    """Return the corpus as a list of plain dicts (order is stable)."""
    return [dict(c) for c in PROBE_CASES]


def _self_test():
    """The corpus must be able to be wrong: fixed checks on its own shape."""
    problems = []
    seen_ids = set()
    for case in PROBE_CASES:
        cid = case.get("id")
        if not cid or cid in seen_ids:
            problems.append("missing or duplicate id: %r" % (cid,))
        seen_ids.add(cid)
        if case.get("shape") not in SHAPES:
            problems.append("%s: unknown shape %r" % (cid, case.get("shape")))
        if case.get("expected_verdict") not in ("pass", "fail"):
            problems.append("%s: verdict must be pass|fail" % cid)
        reason = case.get("expected_reason")
        if not reason:
            problems.append("%s: no expected_reason (a bare verdict is not an expectation)" % cid)
        elif reason not in REASON_CODES:
            problems.append("%s: unknown reason code %r" % (cid, reason))
        if case.get("expected_verdict") == "fail" and reason == "OK":
            problems.append("%s: fail verdict with OK reason" % cid)
        if case.get("expected_verdict") == "pass" and reason != "OK":
            problems.append("%s: pass verdict must explain legality with OK" % cid)
        if not case.get("basis"):
            problems.append("%s: no basis clause" % cid)
        # payloads must be JSON-serialisable: they are what a child would emit
        try:
            json.dumps(case["payload"])
        except Exception as exc:  # pragma: no cover - guarded by the assertion below
            problems.append("%s: payload not serialisable: %s" % (cid, exc))

    shapes = {c["shape"] for c in PROBE_CASES}
    for shape in REQUIRED_SHAPES:
        if shape not in shapes:
            problems.append("corpus covers no %r shape" % shape)
    if len(PROBE_CASES) < 4:
        problems.append("corpus has %d probes, brief requires at least 4" % len(PROBE_CASES))
    verdicts = {c["expected_verdict"] for c in PROBE_CASES}
    if verdicts != {"pass", "fail"}:
        problems.append("corpus verdicts are %s; it must mix both or it can rubber-stamp" % (verdicts,))
    reasons = {c["expected_reason"] for c in PROBE_CASES if c["expected_verdict"] == "fail"}
    if len(reasons) < 4:
        problems.append("only %d distinct failure reasons; a single-code corpus proves nothing" % len(reasons))
    return problems


def _load_resolver():
    """Import the implementation lazily, so this file is valid before it exists."""
    src = os.path.join(ROOT, "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    import n2_resolve  # noqa: F401  (import-time failure is handled by the caller)

    return n2_resolve


def _verify():
    """Run the real validator over the corpus and compare verdict AND reason."""
    try:
        n2_resolve = _load_resolver()
    except Exception as exc:
        print("PRECONDITION src/n2_resolve.py is not importable: %s" % exc)
        print("RESULT: PRECONDITION UNMET")
        return 2

    rows = []
    failures = 0
    for case in PROBE_CASES:
        decision = n2_resolve.evaluate(case["payload"])
        ok = (decision.verdict == case["expected_verdict"]
              and decision.reason == case["expected_reason"])
        if not ok:
            failures += 1
        rows.append((case["id"], case["shape"], case["expected_verdict"],
                     case["expected_reason"], decision.verdict, decision.reason, ok))

    print("== malformed probe corpus vs implementation ==")
    print("%-44s %-20s %-14s %-32s %-14s %-32s %s" % (
        "probe", "shape", "expect", "expect_reason", "actual", "actual_reason", "="))
    for row in rows:
        print("%-44s %-20s %-14s %-32s %-14s %-32s %s" % (
            row[0], row[1], row[2], row[3], row[4], row[5], "ok" if row[6] else "MISMATCH"))
    print()
    print("probes    : %d" % len(rows))
    print("mismatch  : %d" % failures)
    print()
    if failures:
        print("RESULT: MISMATCHES PRESENT")
        return 1
    print("RESULT: ALL PASS")
    return 0


def _dump_json():
    print(json.dumps({"canonical_live_entry": CANONICAL_LIVE_ENTRY,
                      "reason_codes": list(REASON_CODES),
                      "cases": cases()}, indent=2, ensure_ascii=False))
    return 0


def _table():
    print("== n2 malformed-probe corpus (expectations only, written before implementation) ==")
    print("canonical live entry: %s" % CANONICAL_LIVE_ENTRY)
    print("%-44s %-20s %-6s %-32s %s" % ("probe", "shape", "want", "want_reason", "note"))
    for case in PROBE_CASES:
        print("%-44s %-20s %-6s %-32s %s" % (
            case["id"], case["shape"], case["expected_verdict"],
            case["expected_reason"], case["note"]))
    print()
    print("probes: %d (pass %d / fail %d)" % (
        len(PROBE_CASES),
        sum(1 for c in PROBE_CASES if c["expected_verdict"] == "pass"),
        sum(1 for c in PROBE_CASES if c["expected_verdict"] == "fail")))
    return 0


KNOWN_FLAGS = ("--self-test", "--verify", "--json")


def main(argv):
    # A typo'd flag must not fall through to the default table and exit 0: a reader
    # who typed `--verfy` and saw green read a verdict that was never computed.
    unknown = [a for a in argv if a.startswith("-") and a not in KNOWN_FLAGS]
    if unknown:
        print("PRECONDITION unknown argument(s): %s (known: %s)"
              % (", ".join(unknown), ", ".join(KNOWN_FLAGS)))
        print("RESULT: PRECONDITION UNMET")
        return 2
    if "--self-test" in argv:
        problems = _self_test()
        for p in problems:
            print("  FAIL  %s" % p)
        print("corpus self-test: %d probes, %d problems" % (len(PROBE_CASES), len(problems)))
        if problems:
            print("RESULT: FAILURES PRESENT")
            return 1
        print("RESULT: ALL PASS")
        return 0
    if "--verify" in argv:
        return _verify()
    if "--json" in argv:
        return _dump_json()
    return _table()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
