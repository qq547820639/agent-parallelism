#!/usr/bin/env python3
"""Self-check for the agent-parallelism skill.

RUN THIS SCRIPT DIRECTLY. Do not read it into context.
Stdlib only. No network. Read-only. Exit 0 = all pass, 1 = failures.

    python3 scripts/selfcheck.py

It encodes the invariants that a previous full audit had to check by hand.
Those checks found 3 high-severity defects, all of the same class: the same
fact written differently in different files. This script exists to make that
class of drift detectable in one command instead of by manual cross-reading.

Checks are invariants, not snapshots -- they stay valid as content evolves.
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Facts that must appear in BOTH SKILL.md and the number reference.
SHARED_FACTS = [
    "25.6", "14.7", "17.2×", "4.4×", "44.2%", "15.6%", "9.4%",
    "60.2%", "55.1%", "54.0%", "0.41", "45%", "12.6 个百分点", "20k",
]

# Values superseded by primary sources; must not reappear in SKILL.md.
STALE_VALUES = ["26.3", "26.7", "14.3%"]

# JSON schema shared by contract and brief templates.
SCHEMA_FIELDS = {
    "finding", "evidence", "confidence", "blast_radius",
    "verification_passed", "verification_command", "blockers",
}

results = []
warnings = []


def chk(name, cond, detail=""):
    results.append((bool(cond), name, detail))


def warn(name, detail=""):
    """Non-fatal: reported but does not fail the run."""
    warnings.append((name, detail))


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def main():
    skill = read("SKILL.md")
    ref_num = read("references/关键数字速查.md")
    contract = read("assets/contract-template.md")
    brief = read("assets/subagent-brief-template.md")
    checklist = read("assets/dispatch-checklist.md")
    log_csv = read("assets/parallel-log.csv")
    eval_readme = read("evals/README.md")

    # ---------- frontmatter / spec limits ----------
    m = re.match(r"^---\n(.*?)\n---\n", skill, re.S)
    chk("SKILL.md starts with YAML frontmatter", bool(m))
    fm_text = m.group(1) if m else ""
    try:
        import yaml
        fm = yaml.safe_load(fm_text)
    except ImportError:
        # Minimal fallback: only parse the scalar keys this script needs.
        fm = {}
        for line in fm_text.split("\n"):
            if ":" in line and not line.startswith(" "):
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip().strip('"')
        warn("PyYAML not installed -- frontmatter checked with the minimal fallback",
             "pip install pyyaml for exact validation")
    except Exception as exc:
        fm = {}
        chk("frontmatter parses as YAML", False, type(exc).__name__)

    name = fm.get("name", "")
    chk("name matches directory name", name == os.path.basename(ROOT),
        "%r vs %r" % (name, os.path.basename(ROOT)))
    chk("name is lowercase-hyphen, <=64 chars",
        bool(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", str(name))) and len(str(name)) <= 64)

    desc = str(fm.get("description", ""))
    chk("description <=1024 chars", 0 < len(desc) <= 1024, "%d" % len(desc))
    combined = len(desc) + len(str(fm.get("when_to_use", "")))
    chk("description + when_to_use <=1536 (Claude Code listing cap)",
        combined <= 1536, "%d" % combined)
    chk("description states negative cases (Do NOT use)",
        "不要用于" in desc or "Do NOT use" in desc)
    chk("SKILL.md under 500 lines", skill.count("\n") + 1 < 500,
        "%d lines" % (skill.count("\n") + 1))

    # ---------- every referenced resource exists ----------
    for path in set(re.findall(
            r"`(assets/[\w\-.]+|scripts/[\w\-.]+|references/[\w\-.]+|evals/)`", skill)):
        chk("referenced path exists: %s" % path,
            os.path.exists(os.path.join(ROOT, path.rstrip("/"))))

    # ---------- no superseded values ----------
    for bad in STALE_VALUES:
        chk("no superseded value %r in SKILL.md" % bad, bad not in skill)

    # ---------- cross-file fact agreement ----------
    for fact in SHARED_FACTS:
        chk("fact %r present in SKILL.md and number reference" % fact,
            fact in skill and fact in ref_num)

    # ---------- internal cross-references resolve ----------
    secs = [int(x) for x in re.findall(r"^## (\d+)\.", ref_num, re.M)]
    chk("number reference sections are contiguous from 1",
        secs == list(range(1, len(secs) + 1)), str(secs))
    for ref in set(re.findall(r"§(\d+)", skill)):
        chk("SKILL.md cross-reference §%s resolves" % ref, int(ref) in secs)

    # ---------- structure ----------
    gotchas = [int(x) for x in re.findall(r"^(\d+)\. \*\*", skill, re.M)]
    chk("Gotchas numbered 1..N contiguously",
        gotchas == list(range(1, len(gotchas) + 1)), str(gotchas))
    chk("every '##' section is preceded by a '---' rule",
        skill.count("\n## ") == skill.count("---\n\n## "),
        "%d sections, %d rules" % (skill.count("\n## "), skill.count("---\n\n## ")))

    # ---------- shared JSON schema between contract and brief ----------
    ct_fields = set(re.findall(r'"(\w+)":', contract))
    bt_fields = set(re.findall(r'"(\w+)":', brief))
    chk("contract and brief declare the same JSON schema",
        ct_fields == bt_fields, "diff=%s" % sorted(ct_fields ^ bt_fields))
    chk("that shared schema equals the intended 7 fields",
        ct_fields == SCHEMA_FIELDS, "diff=%s" % sorted(ct_fields ^ SCHEMA_FIELDS))

    # ---------- speedup definition + sampling threshold ----------
    chk("log records serial_seconds and parallel_seconds",
        "serial_seconds" in log_csv and "parallel_seconds" in log_csv)
    chk("log speedup denominator includes merge and rework",
        "merge_minutes" in log_csv and "rework_minutes" in log_csv
        and "serial_seconds /" in log_csv.replace("\n", " "))
    chk("log requires >=5 distinct N (matches the script's dof gate)",
        "至少 5 个不同 N" in log_csv)

    # ---------- agentic-drift countermeasure is propagated ----------
    chk("contract requires 先查再写", "先查再写" in contract)
    chk("brief discipline requires 先查再写", "先查再写" in brief)
    chk("checklist requires 先查再写", "先查再写" in checklist)
    chk("checklist includes a dependency-DAG gate", "依赖图 DAG" in checklist)
    chk("checklist paths carry the assets/ prefix",
        "assets/contract-template.md" in checklist
        and "assets/subagent-brief-template.md" in checklist
        and "assets/parallel-log.csv" in checklist)

    # ---------- ownership table is not mislabelled as isolation ----------
    chk("contract does not call the owner table physical isolation",
        "软约束，不构成隔离" in contract)

    # ---------- selection vs synthesis rule is stated ----------
    chk("SKILL.md states 同题择优、异题拼装",
        "同题择优" in skill and "异题拼装" in skill)

    # ---------- eval assets ----------
    for f in ("evals/evals.json", "evals/eval_queries.json"):
        try:
            json.load(open(os.path.join(ROOT, f), encoding="utf-8"))
            chk("%s is valid JSON" % f, True)
        except Exception as exc:
            chk("%s is valid JSON" % f, False, type(exc).__name__)

    try:
        ev = json.load(open(os.path.join(ROOT, "evals/evals.json"), encoding="utf-8"))
        chk("evals.json declares skill_name", ev.get("skill_name") == name)
        chk("evals.json cases all carry assertions",
            all(c.get("assertions") for c in ev.get("evals", [])))
        # Intent-based, not exact-phrase: the prohibitions may be phrased in
        # several ways. Brittle string matching here would repeat the mistake
        # the eval guide warns against.
        blob = [" ".join(c["assertions"]) for c in ev["evals"]]
        isolation = any(("worktree" in b) or ("物理隔离" in b) for b in blob)
        no_fusion = any(("择优" in b) or ("揉成一份" in b) or ("融合" in b) for b in blob)
        no_consensus = any(("多数一致" in b) for b in blob)
        chk("evals.json covers the isolation-procedure axis", isolation)
        chk("evals.json covers the no-fusion prohibition", no_fusion)
        chk("evals.json covers the no-consensus prohibition", no_consensus)
    except Exception as exc:
        chk("evals.json structure readable", False, type(exc).__name__)

    try:
        q = json.load(open(os.path.join(ROOT, "evals/eval_queries.json"), encoding="utf-8"))
        chk("eval_queries.json ids are unique",
            len({x["id"] for x in q}) == len(q))
        chk("eval_queries.json has both positive and negative cases",
            any(x["should_trigger"] for x in q) and any(not x["should_trigger"] for x in q))
        chk("eval_queries.json covers the tool-level-parallel near miss",
            any("xdist" in x["query"] for x in q))
    except Exception as exc:
        chk("eval_queries.json structure readable", False, type(exc).__name__)

    # ---------- stale version tags ----------
    chk("evals/README.md carries no stale version tag", "v1.9" not in eval_readme)

    # ---------- no orphan files ----------
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT)
            if rel == "SKILL.md":
                continue
            chk("file is non-empty: %s" % rel, os.path.getsize(full) > 0)

    # ---------- persistent memory block agrees with the skill ----------
    mem_path = os.path.expanduser("~/.workbuddy/MEMORY.md")
    if os.path.exists(mem_path):
        mem = open(mem_path, encoding="utf-8").read()
        if "agent-parallelism" in mem:
            chk("MEMORY.md gate points at this skill",
                "skills/agent-parallelism/SKILL.md" in mem)
            chk("MEMORY.md K defaults match SKILL.md (read 8~9 / write 2~4)",
                "只读 8~9" in mem and "写入 2~4" in mem
                and "8~9" in skill and "2~4" in skill)
            chk("MEMORY.md carries the wall-clock caveat",
                "准确率不是墙钟时间" in mem or "准确率不是墙钟" in mem)
            chk("MEMORY.md separates rate limiting from coordination",
                "限流" in mem and "协调成本" in mem)

    # ---------- report ----------
    failed = [r for r in results if not r[0]]
    print("== agent-parallelism self-check ==")
    print("checks run : %d" % len(results))
    print("passed     : %d" % (len(results) - len(failed)))
    print("failed     : %d" % len(failed))
    if warnings:
        print("warnings   : %d (non-fatal)" % len(warnings))
        for wname, wdetail in warnings:
            print("  WARN  %s%s" % (wname, ("  <- " + wdetail) if wdetail else ""))
    if failed:
        print()
        for _, name, detail in failed:
            print("  FAIL  %s%s" % (name, ("  <- " + detail) if detail else ""))
        print()
        print("RESULT: FAILURES PRESENT")
        return 1
    print()
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
