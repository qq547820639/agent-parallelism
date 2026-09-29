#!/usr/bin/env python3
"""Canonical live tool-call entry: discovery, canonicalization, name classification.

Pure module: no I/O, no subprocess, no network, stdlib only.  Python 3.9+.

This is the registry half of the n2 closed-world variant.  The registry is not a
guess: every set below was read off this host this session, and the readouts are
recorded in `PROVENANCE` so `checks/n2_check.py` can demand that the live class be
backed by real tool_use/tool_result pairs in the transcript rather than by a claim
in this file.

`Registry` is a value object, and `canonicalize`/`classify`/`resolve_wrapper` all
take one, so a test can hand the resolver a registry that DOES expose a live
flat-name dispatcher.  Without that, the "wrapper resolves" branch is untestable
and the instrument would look right only because every wrapper it knows is dead.

Classes a canonicalised name can land in:

    canonical      the one live entry a nested declarative call may be wrapped in
    live           a live entry that is not the canonical one
    wrapper        a live dispatcher whose reach is a namespace, not the entry
    wrapper_dead   a wrapper-shaped name that is not live at all here
    alias          a non-canonical spelling that denotes some live entry
    opaque         the host's <｜…｜> marker carrying only a call id
    absent         no registry this session read contains it
"""

import re
import unicodedata

# ---------------------------------------------------------------------------
# The decision, taken from host docs before any implementation was written.
# ---------------------------------------------------------------------------
CANONICAL_LIVE_ENTRY = "Bash"

PROVENANCE = {
    "canonical_live_entry": {
        "value": CANONICAL_LIVE_ENTRY,
        "declared_in": "host tool listing for this session: \"Executes a given bash "
                       "command and returns its output.\"",
        "input_shape": "declared input object requires `command` (string); optional "
                       "description, dir_path, run_in_background, timeout",
        "liveness_rule": "must be confirmed by a real tool_use/tool_result pair in the "
                         "transcript; this file only declares the candidate",
    },
    "rejected_alternative": {
        "value": "mcp_call",
        "why": "the brief offers `mcp_call` as the entry for Bash-like hosts, but this "
               "host's own declaration for it is \"Invoke a tool by its fully-qualified "
               "name\" of the form mcp__<server>__<tool>; it therefore cannot reach a "
               "flat command-execution entry and cannot wrap a nested shell payload. "
               "Recorded as a live WRAPPER, not as the canonical entry.",
    },
    "mcp_roster": {
        "read_by": "a real `mcp_list` call this session",
        "total": 139,
        "servers": [
            "plugin_chrome-devtools-mcp_chrome-devtools",
            "qca",
            "builtin",
            "extension-market",
            "browser-use",
            "node-repl",
        ],
        "note": "no `mcp__agent__*` server exists here, so the rollback/restore names in "
                "README.md's sibling-spec table are fabricated in this closed world; the "
                "transcript of that call is the evidence, not this comment.",
    },
}

# Flat names the host declares directly (this session's tool listing).
DIRECT_LIVE_TOOLS = frozenset({
    "Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch",
    "NotebookEdit", "Skill", "TaskCreate", "TaskGet", "TaskList", "TaskStop",
    "TaskUpdate", "ImageGen", "SearchKnowledge", "mcp_call", "mcp_get", "mcp_list",
})

# Namespaces that a real `mcp_list` call read back this session.
LIVE_MCP_SERVERS = frozenset(PROVENANCE["mcp_roster"]["servers"])

# Exact namespaced tools the corpus reasons about (all read from that roster).
MCP_LIVE_TOOLS = frozenset({
    "mcp__node-repl__node_repl",
    "mcp__builtin__list_chat_sessions",
    "mcp__qca__list_agents",
    "mcp__browser-use__list_pages",
})

# Live names that are dispatchers into the mcp__ namespace rather than entries.
LIVE_WRAPPERS = frozenset({"mcp_call"})
# What each live wrapper can reach: a name prefix of the inner target.
WRAPPER_REACH = {"mcp_call": "mcp__"}

# Wrapper-shaped names that this host does not expose at all.
DEAD_WRAPPERS = frozenset({"Tool", "run_tool", "dispatch_tool", "tool_proxy", "CallTool"})

# Non-canonical spellings that denote a live entry.  Canonicalization never folds
# case, so every spelling variant has to be listed to be *recognised as an alias*
# (and refused) instead of falling through to "absent".
ALIAS_OF = {
    "bash": "Bash",
    "BASH": "Bash",
    "Bash_1": "Bash",
    "shell": "Bash",
    "run_shell": "Bash",
    "run_in_terminal": "Bash",
    "terminal": "Bash",
    "BashTool": "Bash",
    "read": "Read",
    "READ": "Read",
    "web_fetch": "WebFetch",
    "functions.Bash": "Bash",
}

MCP_NS = "mcp__"

# The host renders a live call as <｜call_xxx｜>; a marker whose content is an opaque
# id names nothing, so it is its own class rather than "absent".
_OPAQUE_RE = re.compile(r"^(call|toolu|tool[_-]?use|fn|function|id)[_.\-]", re.IGNORECASE)
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]*$")

CANONICAL = "canonical"
LIVE = "live"
WRAPPER = "wrapper"
WRAPPER_DEAD = "wrapper_dead"
ALIAS = "alias"
OPAQUE = "opaque"
ABSENT = "absent"


class Registry(object):
    """The set of names this host actually has, plus what each one is.

    `wrapper_reach` maps a live wrapper to the name prefix it may dispatch into.
    A wrapper resolves onto the canonical entry only when the entry's own name sits
    inside that prefix -- which is why `mcp_call` (reach `mcp__`) can never resolve
    onto `Bash`, while a hypothetical flat dispatcher (`reach ""`) can.
    """

    def __init__(self, canonical_entry=CANONICAL_LIVE_ENTRY,
                 direct_live=DIRECT_LIVE_TOOLS, mcp_servers=LIVE_MCP_SERVERS,
                 mcp_tools=MCP_LIVE_TOOLS, live_wrappers=LIVE_WRAPPERS,
                 wrapper_reach=WRAPPER_REACH, dead_wrappers=DEAD_WRAPPERS,
                 alias_of=None, input_schemas=None):
        self.canonical_entry = canonical_entry
        self.direct_live = frozenset(direct_live)
        self.mcp_servers = frozenset(mcp_servers)
        self.mcp_tools = frozenset(mcp_tools)
        self.live_wrappers = frozenset(live_wrappers)
        self.wrapper_reach = dict(wrapper_reach)
        self.dead_wrappers = frozenset(dead_wrappers)
        self.alias_of = dict(ALIAS_OF if alias_of is None else alias_of)
        # Declared input requirements, from the host docs read this session.
        self.input_schemas = dict(input_schemas or {
            "Bash": {"command": str},
            "mcp_call": {"toolName": str},
        })

    # -- name plumbing ------------------------------------------------------
    def canonicalize(self, name):
        return canonicalize(name)

    def is_live_namespaced(self, name):
        if name in self.mcp_tools:
            return True
        if not name.startswith(MCP_NS):
            return False
        rest = name[len(MCP_NS):]
        if "__" not in rest:
            return False
        return rest.split("__", 1)[0] in self.mcp_servers

    def _namespaced_alias_target(self, name):
        """`mcp__<dead-server>__<live flat name>` is an alias, not a live tool."""
        if not name.startswith(MCP_NS) or "__" not in name[len(MCP_NS):]:
            return None
        _server, _, tool = name[len(MCP_NS):].rpartition("__")
        return tool if tool in self.direct_live else None

    def classify(self, raw_name):
        """Return (class, target).  `target` is the live entry an alias denotes."""
        name = canonicalize(raw_name)
        if not name:
            return ABSENT, ""
        if name == self.canonical_entry:
            return CANONICAL, name
        if name in self.live_wrappers:
            return WRAPPER, name
        if name in self.dead_wrappers:
            return WRAPPER_DEAD, name
        if self.is_live_namespaced(name):
            return LIVE, name
        if name in self.direct_live:
            return LIVE, name
        if name in self.alias_of:
            return ALIAS, self.alias_of[name]
        alias_target = self._namespaced_alias_target(name)
        if alias_target is not None:
            return ALIAS, alias_target
        if _OPAQUE_RE.match(name):
            return OPAQUE, name
        if not _NAME_RE.match(name):
            return ABSENT, name
        return ABSENT, name

    def input_schema(self, raw_name):
        return dict(self.input_schemas.get(canonicalize(raw_name), {}))

    def wrapper_resolves(self, wrapper_name, inner_name):
        """(resolves_to_canonical, canonicalised inner name)."""
        inner = canonicalize(inner_name)
        reach = self.wrapper_reach.get(wrapper_name)
        if not reach:                      # "" or None: a dispatcher over flat names
            return inner == self.canonical_entry, inner
        return inner.startswith(reach) and inner == self.canonical_entry, inner


def canonicalize(name):
    """Strip mechanical wire noise only.  Never edits letters, never folds case.

    Steps, in order: NFKC (compatibility-decode the full-width forms), strip
    surrounding whitespace, then remove ONE enclosing pair of the host's <｜ … ｜>
    call markers.  What is left must match a registered name EXACTLY -- that is the
    whole content of "after canonicalization".
    """
    if not isinstance(name, str):
        return ""
    text = unicodedata.normalize("NFKC", name)
    text = text.strip()
    if text.startswith("<｜") and text.endswith("｜>"):
        text = text[len("<｜"):-len("｜>")].strip()
    if text.startswith("<|") and text.endswith("|>"):
        text = text[2:-2].strip()
    return text.strip()


DEFAULT_REGISTRY = Registry()
