#!/usr/bin/env python3
"""Read the host's session transcript and pair tool calls with their results.

Pure module: it only turns JSONL lines into records; no judgement about n2 lives
here (that is checks/n2_check.py).  Stdlib only, Python 3.9+.

Record shape is taken from this host's real transcripts, the same reading
`subagent_reopen_audit.py` documents: top-level `type` is "assistant" / "user" / …,
a tool call is a `{type:"tool_use", id, name, input}` block inside
`message.content[]`, and its result is a `{type:"tool_result", tool_use_id, content}`
block on a later record, sometimes also mirrored in the record-level
`toolUseResult` object.  A nested (subagent) transcript marks its records
`isSidechain: true`.
"""

import hashlib
import json


def read_records(path):
    """Yield (lineno, record-or-None) for every non-blank line of a JSONL transcript."""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield lineno, json.loads(line)
            except ValueError:
                yield lineno, None


def load_transcript(path):
    """Return (records, parse_errors).  Unparseable lines are counted, never guessed."""
    records = []
    errors = []
    for lineno, rec in read_records(path):
        if rec is None:
            errors.append(lineno)
        else:
            records.append(rec)
    return records, errors


def blocks(record):
    """The content blocks of a record's message, or [] when there is no list."""
    if not isinstance(record, dict):
        return []
    msg = record.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return content if isinstance(content, list) else []


def tool_calls(records):
    """Every tool_use block: {name, input, tool_use_id, lineno, sidechain, uuid}."""
    out = []
    for index, rec in enumerate(records):
        for blk in blocks(rec):
            if isinstance(blk, dict) and blk.get("type") == "tool_use":
                out.append({
                    "name": blk.get("name"),
                    "input": blk.get("input") if isinstance(blk.get("input"), dict) else {},
                    "tool_use_id": blk.get("id"),
                    "index": index,
                    "sidechain": bool(rec.get("isSidechain")),
                    "uuid": rec.get("uuid"),
                    "session_id": rec.get("sessionId"),
                    "timestamp": rec.get("timestamp"),
                })
    return out


def _flatten_content(content):
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                for key in ("text", "content", "stdout", "stderr", "output"):
                    value = item.get(key)
                    if isinstance(value, str):
                        parts.append(value)
    return "\n".join(parts)


def _record_level_text(record):
    result = record.get("toolUseResult") if isinstance(record, dict) else None
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return _flatten_content([result])
    return ""


def tool_results(records):
    """tool_use_id -> {text, index, kind}.  Later duplicates append (streams)."""
    out = {}
    for index, rec in enumerate(records):
        for blk in blocks(rec):
            if isinstance(blk, dict) and blk.get("type") == "tool_result":
                tid = blk.get("tool_use_id")
                if not tid:
                    continue
                entry = out.setdefault(tid, {"text": "", "index": index, "kind": None})
                entry["text"] += _flatten_content(blk.get("content"))
                entry["index"] = index
        mirror = _record_level_text(rec)
        if mirror:
            mirrored_kind = (rec.get("toolUseResult") or {}).get("kind") \
                if isinstance(rec.get("toolUseResult"), dict) else None
            for blk in blocks(rec):
                if isinstance(blk, dict) and blk.get("type") == "tool_result":
                    tid = blk.get("tool_use_id")
                    if tid and mirrored_kind:
                        out[tid]["kind"] = mirrored_kind
                    if tid and tid in out and not out[tid]["text"]:
                        out[tid]["text"] = mirror
    return out


def paired_calls(records):
    """tool_calls whose tool_use_id has a result record, with both texts attached."""
    results = tool_results(records)
    out = []
    for call in tool_calls(records):
        result = results.get(call["tool_use_id"])
        if result is None:
            continue
        item = dict(call)
        item["result_text"] = result["text"]
        item["result_kind"] = result["kind"]
        item["result_index"] = result["index"]
        out.append(item)
    return out


def calls_named(records, name):
    return [c for c in tool_calls(records) if c.get("name") == name]


def find_call_by_substring(records, name, needles):
    """Live calls of `name` whose serialized input contains every needle."""
    blob_needles = list(needles)
    hits = []
    results = tool_results(records)
    for call in calls_named(records, name):
        blob = json.dumps(call["input"], ensure_ascii=False)
        if all(needle in blob for needle in blob_needles):
            entry = dict(call)
            result = results.get(call["tool_use_id"])
            entry["result_text"] = result["text"] if result else None
            entry["result_index"] = result["index"] if result else None
            hits.append(entry)
    return hits


def file_digest(path):
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
