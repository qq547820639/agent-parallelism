#!/usr/bin/env python3
"""Structural parsing of a nested child's declarative tool_call envelope.

Pure module: no I/O, no subprocess, no network, stdlib only.  Python 3.9+.

Contract (declared in scripts/malformed_probe_cases.py, which was written first):

    {"tool_call": {                     # required key; must be an object
        "name": "<str>",                # required; non-empty after canonicalization
        "input": { ... },               # required; must be an object
        "invocation_id": "<str>",       # optional; str when present
        "child": {"agent": "<str>",     # optional; object, depth int >= 1
                  "depth": <int>}}}

Nothing else may appear inside `tool_call`.  Keys BESIDE `tool_call` are fine: a
child legitimately sends prose next to its declarative block.  Whether the name
inside a well-shaped envelope is legal is the resolver's job (`n2_resolve.py`),
which is why this module reports only E_NO_ENVELOPE / E_MALFORMED_ENVELOPE.
"""

ALLOWED_KEYS = frozenset({"name", "input", "invocation_id", "child"})
ALLOWED_CHILD_KEYS = frozenset({"agent", "depth"})

OK = "OK"
E_NO_ENVELOPE = "E_NO_ENVELOPE"
E_MALFORMED_ENVELOPE = "E_MALFORMED_ENVELOPE"


class Envelope(object):
    """A structurally valid envelope.  Carries the raw name; does not judge it."""

    __slots__ = ("name", "input", "invocation_id", "child", "defects")

    def __init__(self, name, input, invocation_id=None, child=None, defects=()):
        self.name = name
        self.input = input
        self.invocation_id = invocation_id
        self.child = child
        self.defects = tuple(defects)

    @property
    def nested_depth(self):
        if isinstance(self.child, dict):
            depth = self.child.get("depth")
            if isinstance(depth, int) and not isinstance(depth, bool):
                return depth
        return None

    def as_dict(self):
        return {"name": self.name, "input": self.input,
                "invocation_id": self.invocation_id, "child": self.child,
                "defects": list(self.defects)}


class EnvelopeError(object):
    """Why a payload cannot even be read as an envelope."""

    __slots__ = ("reason", "detail")

    def __init__(self, reason, detail):
        self.reason = reason
        self.detail = detail

    def __repr__(self):
        return "EnvelopeError(%s, %r)" % (self.reason, self.detail)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def parse_envelope(payload, canonicalize=None):
    """Return an `Envelope` or an `EnvelopeError`.

    `canonicalize` is injected so this module stays free of registry knowledge; it is
    used only to reject a name that canonicalises to nothing (a shape defect).
    """
    if not isinstance(payload, dict):
        return EnvelopeError(E_NO_ENVELOPE,
                             "payload is %s, so it cannot contain a `tool_call` key"
                             % type(payload).__name__)
    if "tool_call" not in payload:
        near = [k for k in payload if "tool" in str(k).lower() or "call" in str(k).lower()]
        return EnvelopeError(E_NO_ENVELOPE,
                             "no `tool_call` key (near-miss keys present: %s)" % (near or "none"))
    raw = payload["tool_call"]
    if not isinstance(raw, dict):
        return EnvelopeError(E_MALFORMED_ENVELOPE,
                             "`tool_call` is %s, expected an object" % type(raw).__name__)

    extra = sorted(set(raw) - ALLOWED_KEYS)
    if extra:
        return EnvelopeError(E_MALFORMED_ENVELOPE,
                             "unknown key(s) inside `tool_call`: %s" % extra)

    if "name" not in raw:
        return EnvelopeError(E_MALFORMED_ENVELOPE, "`name` is missing")
    name = raw["name"]
    if not isinstance(name, str):
        return EnvelopeError(E_MALFORMED_ENVELOPE,
                             "`name` is %s, expected a string" % type(name).__name__)
    if canonicalize is not None and not canonicalize(name):
        return EnvelopeError(E_MALFORMED_ENVELOPE,
                             "`name` canonicalises to the empty string: %r" % name)

    if "input" not in raw:
        return EnvelopeError(E_MALFORMED_ENVELOPE, "`input` is missing")
    tool_input = raw["input"]
    if not isinstance(tool_input, dict):
        return EnvelopeError(E_MALFORMED_ENVELOPE,
                             "`input` is %s, expected an object (a JSON-encoded string is "
                             "not re-parsed)" % type(tool_input).__name__)

    invocation_id = raw.get("invocation_id")
    if invocation_id is not None and not isinstance(invocation_id, str):
        return EnvelopeError(E_MALFORMED_ENVELOPE,
                             "`invocation_id` is %s, expected a string"
                             % type(invocation_id).__name__)

    child = raw.get("child")
    if child is not None:
        if not isinstance(child, dict):
            return EnvelopeError(E_MALFORMED_ENVELOPE,
                                 "`child` is %s, expected an object" % type(child).__name__)
        child_extra = sorted(set(child) - ALLOWED_CHILD_KEYS)
        if child_extra:
            return EnvelopeError(E_MALFORMED_ENVELOPE,
                                 "unknown key(s) inside `child`: %s" % child_extra)
        if "agent" in child and not isinstance(child["agent"], str):
            return EnvelopeError(E_MALFORMED_ENVELOPE, "`child.agent` is not a string")
        if "depth" in child:
            if not _is_int(child["depth"]):
                return EnvelopeError(E_MALFORMED_ENVELOPE,
                                     "`child.depth` is %s, expected an integer"
                                     % type(child["depth"]).__name__)
            if child["depth"] < 1:
                return EnvelopeError(E_MALFORMED_ENVELOPE,
                                     "`child.depth` must be >= 1, got %d" % child["depth"])

    return Envelope(name=name, input=tool_input, invocation_id=invocation_id, child=child)
