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
    # Fitted 2026-09-26 from assets/parallel-log.csv (see references §16).
    "0.697", "0.069", "2.09", "1.43",
    # Read-only round 2 (2026-09-26, 25 arms; see references section 18).
    "0.63~0.83", "25 臂",
    # Mechanism model over the same arms (2026-09-27, references section 19).
    # "27.8~233.9" replaces the retired "27~138" that quoted one level's max
    # as the whole series' upper bound.
    "27.8~233.9", "47.0", "7.23",
    # Out-of-sample mid run (references section 20): the two arms that decided it.
    "2.383", "0.739", "1.64", "0.07",
    # The pin regime (references sections 21-22, 2026-09-27): what the mechanism
    # census, the delivered-concurrency ruler and the regime gate each measured.
    # "40.3" is the ORCHESTRATOR-SIDE term (the dispatch message and the brief gave
    # two different t_start moments); "250.652" only means "the pin serial baseline"
    # because row() now refuses to divide a pin arm by an f0 baseline.
    "40.3", "250.652", "0.35~0.83", "14%", "65510",
    # Draw 2 of the pin regime: the denominator moves inside ONE regime, and the
    # refused arms say how little concurrency the channel actually delivered.
    "112.953", "287.568", "2.55", "0.17~0.22", "1.110", "3.242", "98%",
    # Sections 22 (8)(9)(12): the per-worker cost fit, the channel's kappa floor and
    # the write regime's retrofitted delivery.  "a" belongs to a (corpus x brief) cell
    # -- 47.0 / 101.84 / 21.37 are three cells of one quantity, so quoting 47.0 with no
    # corpus named is the bug these guard.  The pin cell MOVED when draw 5 added 10
    # workers to the same archive (n=32 -> 42): a published fit is a snapshot of a
    # growing file, so the n is part of the token's meaning.
    # The floor is an UPPER edge.  0.155 is NOT "the max of the two residual models" --
    # that max compared a 1000-rep arm (0.1442) with a 400-rep one (0.1547).  At
    # matched reps the two models give 0.1442 / 0.1421 at seed 7, while the
    # per-level model alone spans 0.1382..0.1506 across seeds 7/11/23 -- a wider
    # swing than the model choice, so 0.155 is margin above the measured
    # 0.134..0.151 band, not a computed max, and the edge is not a 4-decimal number.
    # These replaced 0.0386/0.0926/0.093 after the delivery pool stopped inheriting
    # the cost fit's "drop arms whose shards and durations do not pair" rule --
    # which had discarded exactly the partial arms that carry the bad news.
    "101.84", "24.63", "3.00", "0.0464", "0.1442",
    # section 22 (14): the out-of-corpus calibration -- a survives, b does not
    "17.6", "3.08",
    # section 22 (15): the WRITE channel measured its own floor; 0.069 is inside it
    "0.24", "0.0786", "111.7",
    # ⑲ re-derived the read-only bar from 8 arm-A edges (band 0.1614~0.1773) at 0.19;
    # ⑳ moved it again after draw 9 entered the ledger, ㉒ after draw 10's K=6 arm,
    # ㉓ after its K=8 arm (band 0.1659~0.2160 => 0.23), ㉖ after draw 11's K=12 arm
    # plus its 9th denominator (per-level 0.1975~0.2171 / pooled 0.2714~0.2797 => 0.29),
    # and ㉙ DOWN to 0.25 after draw 13's same-window 1/4/8 ladder entered the replay
    # (10 denominators now; band 0.1813~0.2382, per-level 0.21 | pooled 0.25 -- still the
    # pooled fit that sets the line, which is why the pair is printed and not just the max).
    # This list only asserts that the
    # value below APPEARS in SKILL.md -- it does not enforce retirement of old bars
    # (STALE_VALUES below is the retirement list, and 0.21 was deliberately never added
    # to it because a decision bar is a derived quantity: 0.21 was correct, then retired,
    # and can legitimately return if the band widens back past it). The authority for
    # "which bar is live" is `ro_doc_counts.py` C27, which recomputes the bar with
    # ro_ident.bar_from -- round(max(edges)+0.0151, 2), NOT a ceiling, see task note in
    # RUNPLAN -- and fails the face if it disagrees.
    # KNOWN WEAKNESS of this token, registered not hidden: `in` is a SUBSTRING test, so
    # "0.23" is satisfied by any longer number that happens to contain it (0.2311, 10.23).
    # C27 is what actually checks the claim; this line only catches "the face dropped the
    # bar sentence entirely". Narrowing it to a delimited match needs the same decision on
    # all ~40 tokens, so it is a separate change, not something to slip in beside a bar move.
    # 0.23 is NOT added to STALE_VALUES: a decision bar is a derived quantity, and the face
    # still carries it inside ㉓'s narration. Retirement belongs to the reconciler, not here.
    # 0.29 likewise: it was live from ㉖ until draw 13's arms came in, and it can legitimately
    # return if the band widens back past it.
    "0.25", "0.1421",
    # section 22 (11): the two arms that clear the delivery gate. 1.090 is the one
    # with a FAST denominator, 3.242 the one with a SLOW one -- same level, same
    # delivery quality, 3x apart, which is the whole "denominator moves the level"
    # claim in two numbers.
    "1.090", "2.166",
]

# Values superseded by primary sources; must not reappear in SKILL.md's live text
# (a struck-through retraction of them is allowed and expected).
# "至少 5 个不同并发度" is the dof gate the script moved from 5 to 6 (section 18);
# it lived on the README face -- which the stale scan used to skip, so the same
# claim was correct in SKILL.md and wrong one file over.
STALE_VALUES = ["26.3", "26.7", "14.3%", "27~138", "12~14 分钟", "50.3",
                "至少 5 个不同并发度",
                # fully retired (the drift history in the reference face keeps the
                # superseded a-values as LEGITIMATE prose, so they are not listed here)
                "0.745", "5.18"]

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
    # A retired number may still appear INSIDE a strikethrough retraction -- that is
    # the record, not a regression. Scan the live face, and prove the exclusion has
    # teeth on a fixture rather than trusting it (a blanket `~~` strip that ate the
    # whole file would pass every stale check forever).
    skill_live = re.sub(r"~~.*?~~", "", skill, flags=re.S)
    fixture = "live token stays ~~SENTINEL retired~~"
    stripped = re.sub(r"~~.*?~~", "", fixture, flags=re.S)
    chk("retraction stripping is real: removes the struck copy, keeps the prose",
        "SENTINEL" not in stripped and "SENTINEL" in fixture and "live token stays" in stripped,
        repr(stripped))
    readme_path_early = os.path.join(ROOT, "README.md")
    readme = (open(readme_path_early, encoding="utf-8").read()
              if os.path.exists(readme_path_early) else "")
    readme_live = re.sub(r"~~.*?~~", "", readme, flags=re.S)
    for bad in STALE_VALUES:
        chk("no superseded value %r in SKILL.md (live text)" % bad, bad not in skill_live)
        if bad in skill:
            chk("superseded %r survives only as a retraction" % bad,
                bad in skill and bad not in skill_live)
        if readme:
            chk("no superseded value %r in README.md (live text)" % bad,
                bad not in readme_live)
            if bad in readme:
                chk("superseded %r survives in README only as a retraction" % bad,
                    bad not in readme_live)

    # ---------- cross-file fact agreement ----------
    for fact in SHARED_FACTS:
        chk("fact %r present in SKILL.md and number reference" % fact,
            fact in skill and fact in ref_num)

    # The repo README is the THIRD face that quotes these numbers, and it is a
    # repo-only file (the skill source tree has no README.md). Silence is not a
    # verdict: section 21 item 4 is exactly the "absent reads as clean" defect, so
    # the gate states which face it looked at, and in the source tree asserts the
    # thing that makes "no README here" legitimate.
    README_FACTS = ["47.0", "250.652", "40.3", "0.35~0.83", "14%",
                    "112.953", "287.568", "2.55", "0.17~0.22",
                    "1.110", "3.242", "98%",
                    "101.84", "24.63", "4.285", "3.00",
                    "0.0464", "0.1442", "0.0440", "1.090", "2.166",
                    "0.84", "0.55", "0.069"]
    readme_path = os.path.join(ROOT, "README.md")
    if os.path.exists(readme_path):
        readme = open(readme_path, encoding="utf-8").read()
        for fact in README_FACTS:
            chk("README face carries %r" % fact, fact in readme,
                "README.md is where users read the headline numbers")
    else:
        chk("no README face in this tree: repo-only file, so README agreement was "
            "NOT checked here (run the repo copy to check it)",
            not os.path.exists(os.path.join(ROOT, "scripts", "sync_from_source.sh")),
            "a sync script next to a missing README means a half-synced tree")

    # ---------- internal cross-references resolve, on EVERY markdown face ----------
    # A "§N" is a promise that a section exists. Two failure modes were both seen
    # in this skill: a promise pointing at a section that never existed
    # ("关键数字速查.md §7.7"), and -- worse -- a checker whose regex stopped at the
    # first number, so "§7.7" passed because §7 does exist. Hence: the dotted part is
    # checked, refs are resolved on every .md file (not just SKILL.md), and citations
    # that point into someone else's paper (arXiv / Figure / Table) are not treated
    # as internal promises.
    md = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".md"):
                rel = os.path.relpath(os.path.join(dirpath, fn), ROOT)
                md[rel] = read(rel)
    chk("markdown discovery found the faces that carry shared facts",
        {"SKILL.md", "references/关键数字速查.md"} <= set(md), str(sorted(md)))

    heads = {}
    for rel, text in md.items():
        s = set()
        for m in re.finditer(r"^#{2,4} (\d+)(?:\.(\d+))?[.、\s]", text, re.M):
            s.add(m.group(1) if m.group(2) is None
                  else "%s.%s" % (m.group(1), m.group(2)))
        heads[rel] = s
    NUMREF = "references/关键数字速查.md"
    secs = sorted(int(x) for x in heads.get(NUMREF, set()) if "." not in x)
    chk("number reference sections are contiguous from 1",
        secs == list(range(1, len(secs) + 1)), str(secs))
    # A "SS" ref binds to ANOTHER file only when that file's name sits right next
    # to it (e.g. "the number reference SS10"). A wide look-back lets an unrelated
    # filename in the same sentence steal the binding -- measured: three refs to
    # the number reference's own SS13/SS15/SS18 were read as refs to SKILL.md and
    # reported as dead links.
    NAMED = re.compile(r"([\w\-./\u4e00-\u9fff]+\.md)[`\s]*\u00a7(\d+(?:\.\d+)?)")
    for rel, text in sorted(md.items()):
        owners = {m.start(2): m.group(1) for m in NAMED.finditer(text)}
        for m in re.finditer(r"\u00a7(\d+)(\.\d+)?", text):
            tok = m.group(1) + (m.group(2) or "")
            ctx = text[max(0, m.start() - 70):m.start()]
            if ctx.endswith("`"):
                # `SS18.7` in prose is a MENTION of a section token (this file
                # literally documents a retired dead link), not a promise that the
                # target exists. Only bare or filename-anchored refs are promises.
                continue
            if re.search(r"arXiv|Figure|Table|https?://|\u8bba\u6587", ctx):
                continue                      # somebody else's document
            owner = owners.get(m.start(1))
            key = None
            if owner is not None:
                key = os.path.normpath(owner).replace(os.sep, "/")
                if key not in heads:
                    hit = [r for r in heads
                           if os.path.basename(r) == os.path.basename(owner)]
                    key = hit[0] if len(hit) == 1 else None
                pool = heads.get(key, set()) if key else set()
            else:
                pool = heads.get(NUMREF, set()) | heads.get(rel, set())
            chk("cross-reference \u00a7%s in %s resolves" % (tok, rel), tok in pool,
                "owner=%s targets=%s" % (key or "-", sorted(pool)[:8]))

    # ---------- markdown table rows keep their column count ----------
    # Twice now a hand-spliced row added a 7th cell to a 6-column table (once by a
    # trailing pipe, once by an unescaped "|" inside quoted text). Inline code and
    # math make the pipe unavoidable, so the rule is: an UNESCAPED pipe separates
    # cells; "\|" is content.
    for rel, text in sorted(md.items()):
        block = []
        for lineno, line in enumerate(text.split("\n") + [""], 1):
            if line.lstrip().startswith("|"):
                block.append((lineno, line))
            else:
                if len(block) >= 2:
                    widths = {}
                    for ln, row in block:
                        cells = len(re.findall(r"(?<!\\)\|", row))
                        widths.setdefault(cells, []).append(ln)
                    chk("%s table at line %d has one column count per row"
                        % (rel, block[0][0]),
                        len(widths) == 1,
                        "counts=%s" % {k: v[:4] for k, v in widths.items()})
                block = []

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
    # The script owns the dof gate; both documents must quote ITS number.
    fit_src = read("scripts/fit_kappa.py")
    m_gate = re.search(r"MIN_POINTS_FOR_PARAMS\s*=\s*(\d+)", fit_src)
    gate = int(m_gate.group(1)) if m_gate else None
    chk("fit script declares a dof gate", gate is not None)
    chk("SKILL.md quotes the script's own dof gate",
        gate is not None and ("≥%d 个不同 N" % gate) in skill)
    chk("log quotes the script's own dof gate",
        gate is not None and ("至少 %d 个不同 N" % gate) in log_csv)

    # ---------- markdown table integrity ----------
    # A blank line between two rows silently splits a table in two, and every
    # renderer then drops the header from the second block. This project's
    # evidence files are mostly tables patched by scripts, so it is checked.
    for rel in ("SKILL.md", "references/关键数字速查.md", "README.md",
                "evals/README.md", "assets/contract-template.md",
                "assets/subagent-brief-template.md", "assets/dispatch-checklist.md"):
        if not os.path.exists(os.path.join(ROOT, rel)):
            continue        # README.md exists only in the distribution repo
        ls = read(rel).split("\n")
        splits = [k for k in range(1, len(ls) - 1)
                  if not ls[k].strip() and ls[k - 1].lstrip().startswith("|")
                  and ls[k + 1].lstrip().startswith("|")]
        chk("no table split by a blank line: %s" % rel, not splits,
            "lines %s" % [k + 1 for k in splits])

    # ---------- measured USL rows ----------
    data = [l.split(",") for l in log_csv.splitlines()
            if l and not l.startswith("#") and not l.startswith("N,")]
    data = [c for c in data if len(c) >= 8]
    chk("log holds parsed measurement rows", len(data) >= 1, "%d rows" % len(data))

    broken = []
    for c in data:
        try:
            sp, ser, par = float(c[1]), float(c[2]), float(c[3])
            mm, rm = float(c[4]), float(c[5])
        except ValueError:
            broken.append("unparseable N=%s" % c[0])
            continue
        calc = ser / (par + mm * 60.0 + rm * 60.0)
        if abs(calc - sp) > 2e-3:
            broken.append("N=%s recorded %.3f != %.3f" % (c[0], sp, calc))
    chk("each log row's speedup recomputes from its own columns", not broken, str(broken))

    measured_types = {c[10].strip() for c in data if len(c) > 10}
    chk("measured rows reach the script's dof gate",
        gate is None or len({c[0] for c in data}) >= gate,
        "%d distinct N vs gate %s" % (len({c[0] for c in data}), gate))

    # Re-fit the shipped log and compare with what the table says. Presence
    # checks cannot catch a number that is still printed but no longer the fit.
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("fit_kappa", os.path.join(ROOT, "scripts",
                                                                    "fit_kappa.py"))
    _fk = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_fk)
    pairs = _fk.load_csv(os.path.join(ROOT, "assets", "parallel-log.csv"))
    if len(pairs) >= 3:
        _s, _k, _ = _fk.fit_grid(pairs)
        wrow = [l for l in skill.splitlines() if l.startswith("| 写入 / 共享状态")]
        cells = [c.strip().strip("*") for c in wrow[0].strip("|").split("|")] if wrow else []
        try:
            t_sigma, t_kappa = float(cells[1]), float(cells[2])
        except (ValueError, IndexError):
            t_sigma = t_kappa = None
        chk("table sigma equals a re-fit of the log",
            t_sigma is not None and abs(t_sigma - _s) <= 0.02,
            "table=%s refit=%.4f" % (t_sigma, _s))
        chk("table kappa equals a re-fit of the log",
            t_kappa is not None and abs(t_kappa - _k) <= 0.01,
            "table=%s refit=%.5f" % (t_kappa, _k))
        n_max = _fk.nmax(_s, _k)
        chk("table N_max equals a re-fit of the log",
            n_max is not None and len(cells) > 3 and abs(float(cells[3]) - n_max) <= 0.25,
            "table=%s refit=%.2f" % (cells[3] if len(cells) > 3 else None, n_max or -1))
    else:
        warn("log too short to re-fit and compare with the table", "%d pairs" % len(pairs))

    # A 实测 label is a claim about the data file, so it is checked against it.
    table_rows = [l for l in skill.splitlines()
                  if l.startswith("| 只读 / 独立分片") or l.startswith("| 写入 / 共享状态")
                  or l.startswith("| 编码主链路")]
    chk("Step 2 default table is present with one row per scenario", len(table_rows) == 3,
        "%d rows" % len(table_rows))
    for row in table_rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        basis = cells[-1] if cells else ""
        scenario = cells[0]
        backed = ("write" if "写入" in scenario else
                  "read" if "只读" in scenario else "coding")
        # Test the CLAIM FORM, not the substring: the label is always "**实测**" at
        # the head of the basis cell. A substring test misgrades prose that merely
        # mentions measuring (未实测 / 试测过), which is exactly how this check first
        # fired on a legitimate read-only caveat.
        claims_measured = basis.startswith("**实测**")
        # "有数据" and "这个数是证据" are two different claims. A row can be backed by
        # real dispatches and still carry a fitted parameter that sits inside its own
        # channel's zero-interference floor (section 22 item 15), so that state gets its
        # own label form -- and the label has to name the floor, or "拟合值" is a weasel.
        claims_fitted = basis.startswith("**拟合值，但不是耦合的证据**")
        actually_backed = backed == "write" and any("write" in t for t in measured_types)
        if claims_fitted:
            chk("scenario '%s': the fitted-not-evidence label cites its section" % scenario,
                u"§22 ⑮" in basis, basis[:90])
        chk("scenario '%s': 实测 label matches the data behind it" % scenario,
            (not claims_measured) or actually_backed,
            "label=%s rows=%s measured_types=%s" % (claims_measured, actually_backed,
                                                     sorted(measured_types)))
        chk("scenario '%s': a backed row states which evidence class it is in" % scenario,
            (not actually_backed) or claims_measured or claims_fitted, basis[:90])
        if not claims_measured and not claims_fitted:
            chk("scenario '%s' says its number is unmeasured" % scenario,
                "猜" in basis or "未实测" in basis, basis)

    # SKILL.md promises these readings; if the script stops printing them the
    # promise becomes a lie in the next run someone makes.
    for needle in ("ceiling if kappa=0", "corr(sigma,kappa)", "NOT identifiable",
                   "--selftest"):
        chk("fit script prints what SKILL.md promises: %s" % needle, needle in fit_src)

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

    # ---------- no hardcoded self-check count ----------
    # The number of checks depends on the file tree, so a literal count in
    # SKILL.md goes stale the moment a file is added. It already did once.
    chk("SKILL.md does not hardcode the self-check count",
        not re.search(r"\d+\s*项结构不变量", skill))

    # ---------- no orphan files ----------
    for dirpath, dirnames, filenames in os.walk(ROOT):
        # Descending into .git made the repo copy "check" that git object files
        # are non-empty and read 188 vs the source tree's 97 -- the count was
        # measuring git history, not the skill.
        dirnames[:] = [d for d in dirnames
                       if d != "__pycache__" and not d.startswith(".")]
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
            # K defaults live in the SKILL.md table; MEMORY.md restates them.
            # Compare the two by parsing, so a changed table cannot leave the
            # resident memory quoting a superseded number unnoticed.
            def _kcell(prefix):
                for row in skill.splitlines():
                    if row.startswith(prefix):
                        cells = [c.strip().strip("*") for c in row.strip("|").split("|")]
                        return cells[4] if len(cells) >= 5 else None
                return None

            k_read, k_write = _kcell("| 只读 / 独立分片"), _kcell("| 写入 / 共享状态")
            chk("MEMORY.md read-only K default equals the table",
                k_read is not None and ("只读 %s" % k_read) in mem,
                "table=%r" % k_read)
            chk("MEMORY.md write K default equals the table",
                k_write is not None and ("写入 %s" % k_write) in mem,
                "table=%r" % k_write)
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
