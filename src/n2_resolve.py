#!/usr/bin/env python3
"""The n2 closed-world judge: envelope validator + wrapper resolver, in one call.

Pure module: no I/O, no subprocess, no network, stdlib only.  Python 3.9+.

    from n2_resolve import evaluate
    d = evaluate(payload)            # -> Decision(verdict="pass"|"fail", reason=..., ...)

Rules, all traceable to the brief:

  * A nested declarative tool_call must be wrapped in the canonical live entry
    (`Bash` here, decided in n2_canonical.PROVENANCE from host docs) -- so a
    well-shaped envelope whose canonicalised name is that entry PASSES.
  * All wrappers must resolve to that same entry AFTER canonicalization.  A wrapper
    that does not resolve there FAILS even when its inner call names a live tool;
    `mcp_call -> Bash` is that case, and it is live on this host, so the refusal is
    not an artefact of a made-up dispatcher name.
  * Aliased / wrapped / fabricated live names FAIL.  A direct live call PASSES.
  * Envelopeless payloads FAIL; malformed envelopes FAIL, including one whose name
    is canonical but whose shape is broken -- a name-only validator would wave it
    through, which is why shape is judged before naming.

`registry` is a parameter (default: the names this host really has), so the
"wrapper resolves" branch can be driven with a registry that exposes a live flat
dispatcher.  Otherwise the positive branch is dead code that no probe can light.
"""

import n2_canonical
import n2_envelope as env
from n2_canonical import (ABSENT, ALIAS, CANONICAL, LIVE, OPAQUE, WRAPPER,
                          WRAPPER_DEAD, DEFAULT_REGISTRY)

OK = env.OK
E_NO_ENVELOPE = env.E_NO_ENVELOPE
E_MALFORMED_ENVELOPE = env.E_MALFORMED_ENVELOPE
E_ALIAS = "E_ALIAS"
E_LIVE_NOT_CANONICAL = "E_LIVE_NOT_CANONICAL"
E_FABRICATED_NAME = "E_FABRICATED_NAME"
E_OPAQUE_CALL_ID = "E_OPAQUE_CALL_ID"
E_WRAPPER_NOT_LIVE = "E_WRAPPER_NOT_LIVE"
E_WRAPPER_TARGET_NOT_CANONICAL = "E_WRAPPER_TARGET_NOT_CANONICAL"
E_WRAPPER_TARGET_ABSENT = "E_WRAPPER_TARGET_ABSENT"

REASONS = (OK, E_NO_ENVELOPE, E_MALFORMED_ENVELOPE, E_ALIAS, E_LIVE_NOT_CANONICAL,
           E_FABRICATED_NAME, E_OPAQUE_CALL_ID, E_WRAPPER_NOT_LIVE,
           E_WRAPPER_TARGET_NOT_CANONICAL, E_WRAPPER_TARGET_ABSENT)

FAIL = "fail"
PASS = "pass"


class Decision(object):
    """What the judge concluded, and the material it concluded it from."""

    __slots__ = ("verdict", "reason", "detail", "diagnostics")

    def __init__(self, verdict, reason, detail="", diagnostics=None):
        self.verdict = verdict
        self.reason = reason
        self.detail = detail
        self.diagnostics = dict(diagnostics or {})

    @property
    def ok(self):
        return self.verdict == PASS

    def evidence(self):
        bits = ["reason=%s" % self.reason]
        for key in ("raw_name", "canonical_name", "entry_class", "inner_name",
                    "inner_class", "wrapper_reach", "registry_canonical"):
            if key in self.diagnostics and self.diagnostics[key] not in (None, ""):
                bits.append("%s=%s" % (key, self.diagnostics[key]))
        if self.detail:
            bits.append(self.detail)
        return "%s: %s" % (self.verdict.upper(), "; ".join(bits))

    def as_dict(self):
        out = {"verdict": self.verdict, "reason": self.reason, "detail": self.detail,
               "evidence": self.evidence()}
        out.update(self.diagnostics)
        return out

    def __repr__(self):
        return "Decision(%r, %r)" % (self.verdict, self.reason)


def _decision(reason, detail="", diagnostics=None):
    verdict = PASS if reason == OK else FAIL
    return Decision(verdict, reason, detail, diagnostics)


def _check_input_schema(name, tool_input, registry):
    """The entry's own declared input requirements; [] when the host declares none."""
    schema = registry.input_schema(name)
    problems = []
    for key, kind in sorted(schema.items()):
        if key not in tool_input:
            problems.append("required %r missing" % key)
        elif kind is str and not isinstance(tool_input[key], str):
            problems.append("required %r is %s, expected str"
                            % (key, type(tool_input[key]).__name__))
        elif kind is str and not tool_input[key].strip():
            problems.append("required %r is blank" % key)
    return problems


def evaluate(payload, registry=None):
    """Judge one nested child payload.  Returns a `Decision`; never raises."""
    registry = registry or DEFAULT_REGISTRY
    parsed = env.parse_envelope(payload, canonicalize=registry.canonicalize)
    if isinstance(parsed, env.EnvelopeError):
        return _decision(parsed.reason, parsed.detail,
                         {"stage": "envelope"})

    base = {
        "stage": "resolve",
        "raw_name": parsed.name,
        "canonical_name": registry.canonicalize(parsed.name),
        "registry_canonical": registry.canonical_entry,
        "nested_depth": parsed.nested_depth,
    }
    cls, target = registry.classify(parsed.name)
    base["entry_class"] = cls
    base["alias_target"] = target if cls == ALIAS else None

    if cls == CANONICAL:
        problems = _check_input_schema(parsed.name, parsed.input, registry)
        if problems:
            return _decision(E_MALFORMED_ENVELOPE,
                             "input does not satisfy the entry's declared shape: %s"
                             % ", ".join(problems), base)
        return _decision(OK, "wrapped in the canonical live entry", base)

    if cls == LIVE:
        return _decision(E_LIVE_NOT_CANONICAL,
                         "%r is a live entry but not the canonical one (%r)"
                         % (base["canonical_name"], registry.canonical_entry), base)

    if cls == ALIAS:
        return _decision(E_ALIAS,
                         "%r is an alias of live entry %r, not its registered name"
                         % (base["canonical_name"], target), base)

    if cls == OPAQUE:
        return _decision(E_OPAQUE_CALL_ID,
                         "marker form carries the call id %r and no tool name"
                         % base["canonical_name"], base)

    if cls == WRAPPER_DEAD:
        return _decision(E_WRAPPER_NOT_LIVE,
                         "%r is wrapper-shaped but no live entry on this host"
                         % base["canonical_name"], base)

    if cls == WRAPPER:
        problems = _check_input_schema(parsed.name, parsed.input, registry)
        if problems:
            return _decision(E_MALFORMED_ENVELOPE,
                             "wrapper input is incomplete: %s" % ", ".join(problems), base)
        inner_raw = parsed.input.get("toolName")
        reach = registry.wrapper_reach.get(base["canonical_name"])
        base["wrapper_reach"] = reach
        resolves, inner = registry.wrapper_resolves(parsed.name, inner_raw)
        base["inner_name"] = inner
        inner_cls, inner_target = registry.classify(inner_raw)
        base["inner_class"] = inner_cls
        if resolves:
            return _decision(OK, "wrapper %r resolves onto the canonical live entry"
                             % base["canonical_name"], base)
        if inner_cls in (ABSENT, OPAQUE):
            return _decision(E_WRAPPER_TARGET_ABSENT,
                             "wrapper %r targets %r, which is live nowhere"
                             % (base["canonical_name"], inner), base)
        return _decision(E_WRAPPER_TARGET_NOT_CANONICAL,
                         "wrapper %r (reach %r) targets %r (%s entry%s), not the canonical "
                         "live entry %r -- inner liveness does not rescue a wrapper"
                         % (base["canonical_name"], reach, inner, inner_cls,
                            "" if inner != inner_target else "", registry.canonical_entry),
                         base)

    return _decision(E_FABRICATED_NAME,
                     "%r appears in no registry this session read"
                     % (parsed.name if isinstance(parsed.name, str) else "?",), base)


def verdict_of(payload, registry=None):
    """Convenience for probes and tests: the bare "pass"/"fail" string."""
    return evaluate(payload, registry=registry).verdict


def reason_of(payload, registry=None):
    return evaluate(payload, registry=registry).reason


def canonical_entry(registry=None):
    registry = registry or DEFAULT_REGISTRY
    return n2_canonical.canonicalize(registry.canonical_entry)
