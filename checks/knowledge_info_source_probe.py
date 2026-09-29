"""knowledge_info_source_probe.py — 判定"知识召回工具返回的那份 info 到底是知识卡本身的
内容，还是 overview 区里那些知识卡的清单/摘要"。

问题落在两个互斥假设上：

    H_card      返回体 = 命中的那张/那几张知识卡**自己的正文**（full card body，逐字、不截断）
    H_roster    返回体 = 上下文里 `# Project knowledge overview` 那一段的产物
                （标题树 + 每条 ≤500 字符的摘要前缀 + 全局 6000 字符预算的截断视图）

做法（不复刻判据，直接跑出厂实现）：
  1. 定位本机 `qoder-context` 插件的 `qoder-search.bundle.mjs`。本机承担知识召回的工具名是
     `SearchKnowledge`；任务里写的 `sm_recall_knowledge` 在本机不存在（alias 取证见报告
     `alias_evidence`，扫入域=本仓库 + 插件 bundle 目录，并把探针自己的产物排除掉）。
  2. 复制一份 bundle，只把末尾那一句 CLI 入口语句换成符号交接（执行器见
     `checks/knowledge_info_source_harness.mjs`），用 Node 真跑出厂函数：
       QJt  MCP 工具注册（模型收到的 {content:[{type:"text",text}]} 就是它给的）
       cU   检索入口（mode=search / mode=fetch 两条分支）
       NKe  卡库层（行→hit 的 ZJt、模块命中自动补架构卡的 UJt/Omn）
       PKe  hit → 模型看到的文本
       Ate + UKe + FKe + den/fen/hen/men/pen   overview 注入器（另一条路径）
  3. 同一份合成语料喂两条路径，语料里放五根互不重叠的针：
       卡正文首行 / 卡正文末行 / 第二张卡（架构设计）正文 / 被链接卡标题 / 只在 overview 出现的仓库级标题
     overview 路径跑两档：小预算 maxLength=1900（逼出 FKe 丢行）与真摘要管线
     den→fen→hen（每条 500 字符前缀 + "..."）；工具路径不截。
     于是"哪些正文行只在工具侧活着""仓库级条目是否只出现在 overview 侧"就是可观察分岔。
  4. 判据全部在本文件里算（harness 只交回原文读数）：12 道前提 + 14 条观察 + 9 支
     必开火/必不开火对照（含"把 overview 渲染冒充工具返回体时判据必须翻脸"两支反向对照）。

退出码：0=判据出数且前提与对照全过；1=判据红或对照翻车；2=前提缺失
（bundle 找不到 / 入口锚点不唯一 / Node 不可用 / harness API 漂移 / 截断预算没生效），
前提红一律不给结论，也不许静默降级成"判不出来"。
用法：python3 checks/knowledge_info_source_probe.py [--keep-scratch DIR] [--bundle PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE / "knowledge_info_source_harness.mjs"
PLUGIN_ROOT = Path.home() / ".qoder-cn/plugins/cache/qoderapp-bundler/qoder-context"
BUNDLE_REL = Path("runtime/qoder-search.bundle.mjs")

TREE_GLYPHS = ("├──", "└──")          # overview 树的前缀字符（bundle 常量 MKe/LKe）
MIN_DROPPED_BODY_LINES = 5            # overview 小预算档至少要丢这么多正文行，分岔才算成立


OVERVIEW_TEMPLATE_NEEDLE = "truncated summary, not the full entry"
REPO_SECTION_NEEDLE = "[Repository Knowledge]"


def body_dropped(text: str, fixture: dict) -> list:
    """卡正文里有哪些行没出现在这段文本中（行粒度，不依赖 FKe 的挑行顺序）。"""
    return [ln for ln in fixture["card_content"].splitlines() if ln.strip() and ln not in text]


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------
def build_fixture(nonce: str, body_lines: int = 42, overview_max_length: int = 1900) -> dict:
    """同一份语料喂两条路径；三根针分别属于：卡正文头部 / 卡正文尾部 / 仅 overview 区。

    overview 那条路径的预算由 `Ate` 自己算：`FKe(UKe(u), maxLength - len(xv) - 2)`，
    本机 1.0.72 bundle 的 `len(xv)=785`，所以 maxLength=1900 时正文只留 ~1113 字符，
    4663 字符的卡正文必然被截掉尾巴（该前提由 `check_preconditions` 复核，不靠常量）。
    """
    head = "HEAD-MARKER-%s 模块概览正文第一行，工具返回体必须逐字带上它" % nonce
    tail = "TAIL-MARKER-%s 模块概览正文最后一行，只有不截断的路径才留得住它" % nonce
    middle = ["BODY-PAD-%02d %s" % (i, ("填充行 %d，用来把正文撑到会被 overview 预算丢掉的长度。" % i) * 3)
              for i in range(body_lines - 2)]
    content = "\n".join([head] + middle + [tail])

    arch = "ARCH-BODY-%s 架构设计卡自己的正文，命中模块标题时应作为第二张卡并列返回。" % nonce
    return {
        "nonce": nonce,
        "markers": {
            "head": head,
            "tail": tail,
            "arch": arch,
            "linked_title": "Linked-module-%s" % nonce,
            "repository_only_title": "Repository-card-%s-只在overview区出现" % nonce,
            "empty_probe": "EMPTY-LEAK-%s" % nonce,
        },
        "overview_max_length": overview_max_length,
        "fetch_query": "Probe-module-%s - Overview" % nonce,
        "search_query": "probe module overview %s" % nonce,
        "repository_card_title": "Repository-card-%s-只在overview区出现" % nonce,
        "card": {
            "id": "card-%s-1" % nonce,
            "title": "Probe-module-%s - Overview" % nonce,
            "content": content,
            "cardType": "overview",
            "moduleId": "mod-%s" % nonce,
            "moduleScopes": ["checks/"],
            "links": [{"kind": "module:related_to", "title": "Linked-module-%s" % nonce}],
        },
        "arch": {
            "id": "card-%s-2" % nonce,
            "title": "Probe-module-%s - Architecture Design" % nonce,
            "content": arch,
            "cardType": "architecture_design",
            "moduleId": "mod-%s" % nonce,
            "links": [],
        },
        "card_content": content,
    }


# ---------------------------------------------------------------------------
# bundle location (provenance)
# ---------------------------------------------------------------------------
def find_bundle(explicit: str | None) -> tuple[Path | None, str]:
    if explicit:
        p = Path(explicit)
        return (p, "explicit") if p.is_file() else (None, "explicit path not a file: %s" % p)
    if not PLUGIN_ROOT.is_dir():
        return None, "plugin root absent: %s" % PLUGIN_ROOT
    cands = sorted(PLUGIN_ROOT.glob("*/" + BUNDLE_REL.as_posix()),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    cands = [c for c in cands if c.is_file()]
    if not cands:
        return None, "no qoder-search.bundle.mjs under %s" % PLUGIN_ROOT
    return cands[0], "newest of %d installed bundle(s): %s" % (
        len(cands), ", ".join(str(c.parent.parent.name) for c in cands))


SELF_FILE_MARK = "knowledge_info_source"   # 探针自己的两份文件里必然含这些 needle，排除
SCRATCH_DIR_NAME = ".knowledge-probe"      # 探针跑出来的 report/outputs/copy 也一样含 needle，排除


def alias_readouts(project: Path) -> dict:
    """`sm_recall_knowledge` 这个名字在本机到底存不存在？以及承担召回的是谁。"""
    needles = {"sm_recall_knowledge": 0, "SearchKnowledge": 0}
    hits = {n: [] for n in needles}
    excluded_self = []
    scanned = []
    targets = []
    if project.is_dir():
        targets.append(project)
    if PLUGIN_ROOT.is_dir():
        targets.append(PLUGIN_ROOT)
    for root in targets:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in (".md", ".mjs", ".json", ".js", ".py", ".txt"):
                continue
            if path.stat().st_size > 12_000_000:
                continue
            if SELF_FILE_MARK in path.name or SCRATCH_DIR_NAME in path.parts:
                excluded_self.append(str(path))
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            scanned.append(str(path))
            for n in needles:
                if text.count(n):
                    needles[n] += text.count(n)
                    hits[n].append("%s x%d" % (path.name, text.count(n)))
    return {"needle_counts": needles, "needle_hits_by_file": hits,
            "files_scanned": len(scanned), "self_files_excluded": excluded_self,
            "scanned_roots": [str(r) for r in targets]}


# ---------------------------------------------------------------------------
# the criterion
# ---------------------------------------------------------------------------
def check_preconditions(fixture: dict, texts: dict, data_arms: dict) -> list:
    """预算/针/截断都是前提，不是结论：不成立就整轮拒判（rc=2），不许静默降级成 indeterminate。"""
    m = fixture["markers"]
    tight = texts["overview_tight_budget"] or ""
    default = texts["overview_default_budget"] or ""
    tool = texts["tool_fetch"] or ""
    return [
        ("tight_overview_not_empty", len(tight) > 0, "len=%d" % len(tight)),
        ("tight_overview_keeps_head", m["head"] in tight, "head in tight=%s" % (m["head"] in tight)),
        ("tight_overview_drops_body_lines",
         len(body_dropped(tight, fixture)) >= MIN_DROPPED_BODY_LINES,
         "dropped=%d/%d body lines (FKe packs surviving lines by leftover budget, "
         "so the criterion counts dropped lines instead of assuming the last one goes)"
         % (len(body_dropped(tight, fixture)), len(fixture["card_content"].splitlines()))),
        ("tight_overview_shows_truncation", "showing " in tight,
         repr([ln for ln in tight.splitlines() if "showing" in ln][:1])),
        ("default_overview_keeps_tail", m["tail"] in default, "tail in default=%s" % (m["tail"] in default)),
        ("default_overview_lists_repo_card", m["repository_only_title"] in default,
             "repo-only in default=%s" % (m["repository_only_title"] in default)),
        ("tool_arm_not_empty", len(tool) > 0, "len=%d" % len(tool)),
        # 上游摘要管线（den→fen→hen→men→pen）自己也要能开火，否则"overview 是摘要"这条是读空
        ("pipeline_hen_cuts_long_card",
         data_arms["overview_pipeline"]["long_card"]["ends_with_ellipsis"]
         and data_arms["overview_pipeline"]["long_card"]["keeps_head"]
         and not data_arms["overview_pipeline"]["long_card"]["keeps_tail"]
         and data_arms["overview_pipeline"]["long_card"]["cut_is_prefix_of_body"],
         "long card: cut=%d chars limit=%s ellipsis=%s keeps_head=%s keeps_tail=%s prefix=%s" % (
             data_arms["overview_pipeline"]["long_card"]["cut_length"],
             data_arms["overview_pipeline"]["per_card_cut_limit"],
             data_arms["overview_pipeline"]["long_card"]["ends_with_ellipsis"],
             data_arms["overview_pipeline"]["long_card"]["keeps_head"],
             data_arms["overview_pipeline"]["long_card"]["keeps_tail"],
             data_arms["overview_pipeline"]["long_card"]["cut_is_prefix_of_body"])),
        ("pipeline_hen_leaves_short_card_whole",
         data_arms["overview_pipeline"]["short_card"]["unchanged_by_cut"]
         and not data_arms["overview_pipeline"]["short_card"]["ends_with_ellipsis"],
         "short card: unchanged=%s cut=%d" % (
             data_arms["overview_pipeline"]["short_card"]["unchanged_by_cut"],
             data_arms["overview_pipeline"]["short_card"]["cut_length"])),
        ("pipeline_roots_hold_the_long_card_cut",
         data_arms["overview_pipeline"]["roots_content_len"]
         == data_arms["overview_pipeline"]["long_card"]["cut_length"],
         "roots[0].content len=%s == hen(long card)=%s" % (
             data_arms["overview_pipeline"]["roots_content_len"],
             data_arms["overview_pipeline"]["long_card"]["cut_length"])),
        ("pipeline_repo_cards_are_title_only",
         len(data_arms["overview_pipeline"]["repository_cards"]) == 1
         and not data_arms["overview_pipeline"]["repository_cards_carry_content"],
         "repository_cards=%s carry_content=%s" % (
             data_arms["overview_pipeline"]["repository_cards"],
             data_arms["overview_pipeline"]["repository_cards_carry_content"])),
        ("pipeline_above_threshold_drops_summaries",
         data_arms["overview_above_threshold"]["fen_returns_empty"],
         "modules=%d fen content-map size=%d" % (
             data_arms["overview_above_threshold"]["module_count"],
             data_arms["overview_above_threshold"]["content_map_size"])),
    ]


def judge(fixture: dict, texts: dict, keys: dict, arms: dict | None = None) -> tuple[str, list]:
    """返回 (verdict, rows)；rows = [(观察名, 判据, 读数, 支持哪个假设)]。"""
    m = fixture["markers"]
    body = fixture["card_content"]
    tool = texts["tool_fetch"] or ""
    tool_s = texts["tool_search"] or ""
    ovd_t = texts["overview_tight_budget"] or ""
    ovd_d = texts["overview_default_budget"] or ""
    no_db = texts["tool_fetch_no_db"] or ""
    empty = texts["control_empty_body"] or ""
    nke = texts["store_fetch_via_NKe"] or ""
    arms = arms or {}

    rows = []

    def obs(name, predicate, reading, supports):
        rows.append({"obs": name, "holds": bool(predicate), "reading": reading, "supports": supports})
        return bool(predicate)

    # 1) 工具返回体逐字带上整张卡的正文（含被 overview 预算丢掉的那一行）
    a = obs("tool.body_verbatim",
            body in tool,
            "card.content len=%d, in tool text=%s" % (len(body), body in tool),
            "H_card")
    b = obs("tool.tail_line_survives",
            m["tail"] in tool and m["head"] in tool,
            "head=%s tail=%s" % (m["head"] in tool, m["tail"] in tool),
            "H_card")
    # 2) 同一条正文喂 overview 注入器、按小预算渲染时确实丢行 ⇒ 两路径可观察分岔：
    #    overview 是同一批卡正文的**有损视图**，工具返回体是无损的那一份。
    dropped_t = body_dropped(ovd_t, fixture)
    dropped_d = body_dropped(ovd_d, fixture)
    dropped_tool = body_dropped(tool, fixture)
    c = obs("overview.drops_body_lines_tool_keeps_all",
            len(dropped_t) >= MIN_DROPPED_BODY_LINES and len(dropped_tool) == 0,
            "dropped body lines: tight-overview=%d default-overview=%d tool=%d (of %d)"
            % (len(dropped_t), len(dropped_d), len(dropped_tool),
               len(fixture["card_content"].splitlines())),
            "H_card(分岔成立)")
    d = obs("overview.truncation_marker_present",
            "showing " in ovd_t,
            repr([ln for ln in ovd_t.splitlines() if "showing" in ln][:1]),
            "overview 是截断视图")
    # 2b) overview 那条管线产出的"摘要"是正文前缀（hen 的 500 字符切），工具那份是全文
    p_ = arms["overview_pipeline"]
    obs("overview.summary_is_truncated_prefix_of_body",
        p_["long_card"]["cut_is_prefix_of_body"] and p_["long_card"]["ends_with_ellipsis"]
        and len(p_["long_card"]) > 0,
        "overview per-card cut=%d chars(ellipsis=%s) vs tool body=%d chars" % (
            p_["long_card"]["cut_length"], p_["long_card"]["ends_with_ellipsis"], len(body)),
        "同一份正文：overview 是截断视图，工具是无损视图")

    # 3) 工具返回体不含 overview 段的模板句/树字符/仓库级条目 ⇒ 不是从 overview 抄来的
    e = obs("tool.no_overview_template_text",
            OVERVIEW_TEMPLATE_NEEDLE not in tool and "# Project knowledge overview" not in tool,
            "template-needle=%s header-needle=%s" % (
                OVERVIEW_TEMPLATE_NEEDLE in tool, "# Project knowledge overview" in tool),
            "H_card")
    f = obs("tool.no_overview_tree_glyphs",
            not any(g in tool for g in TREE_GLYPHS) and any(g in ovd_t for g in TREE_GLYPHS),
            "tool has tree glyphs=%s, overview(tight) has=%s" % (
                any(g in tool for g in TREE_GLYPHS), any(g in ovd_t for g in TREE_GLYPHS)),
            "H_card")
    g = obs("tool.omits_overview_only_roster_item",
            m["repository_only_title"] not in tool
            and m["repository_only_title"] in ovd_d,
            "repo-only title in tool=%s in overview(default)=%s" % (
                m["repository_only_title"] in tool, m["repository_only_title"] in ovd_d),
            "H_card")
    # 4) search 档与 fetch 档走同一个序列化器（同一形状，不是清单）
    h = obs("search_arm_same_shape",
            m["tail"] in tool_s and body in tool_s,
            "search text len=%d, body in text=%s" % (len(tool_s), body in tool_s),
            "H_card")
    # 5) 库不存在时返回固定话术，不回落到 overview 段
    i_ = obs("no_db_arm_is_constant_message",
             len(no_db) > 0 and body not in no_db and m["tail"] not in no_db
             and not any(g in no_db for g in TREE_GLYPHS),
             "text=%r" % no_db[:78],
             "info≠overview（无库时也没有清单）")
    # 6) 空正文对照：正文针必须消失，标题壳必须留下 ⇒ 第 1 条不是模板噪声造成的永真
    j = obs("empty_body_control",
            m["tail"] not in empty and m["head"] not in empty
            and fixture["card"]["title"] in empty,
            "empty-arm: title=%s head=%s tail=%s" % (
                fixture["card"]["title"] in empty, m["head"] in empty, m["tail"] in empty),
            "判据有牙")
    # 7) 卡库层：模块标题命中时并列返回多张卡，每张都是自己的正文（overview 卡 + 架构卡）
    k = obs("store_layer_returns_each_card_body",
            m["tail"] in nke and m["arch"] in nke,
            "card-body=%s arch-body=%s" % (m["tail"] in nke, m["arch"] in nke),
            "H_card(命中几张返回几张)")
    # 8) 唯一的"清单式"内容是链接小节：只给标题，不给被链接卡的正文
    l = obs("linked_section_is_title_only",
            ("- [%s]" % m["linked_title"]) in tool and m["empty_probe"] not in tool,
            "bullet present=%s" % (("- [%s]" % m["linked_title"]) in tool),
            "返回体里唯一像清单的一栏")
    # 9) 载荷字段名清点：到底有没有一个字面叫 info 的字段
    n = obs("no_literal_info_key",
            not keys["literal_info_found"],
            "hit keys=%s | tool keys=%s | handler keys=%s" % (
                keys["in_hit_builder"], keys["in_tool_result"], keys["in_handler_result"]),
            "命名事实")

    card_side = [a, b, c, d, e, f, g, h, k]
    roster_side = [x for x in (e, f, g) if not x]      # 这三条成立即否证 H_roster
    if all(card_side) and not any(roster_side):
        verdict = "H_card"
    elif (body not in tool) and (m["repository_only_title"] in tool or OVERVIEW_TEMPLATE_NEEDLE in tool):
        verdict = "H_roster"
    else:
        verdict = "indeterminate"
    return verdict, rows


# ---------------------------------------------------------------------------
# self-test: 把判据本身喂正反两种文本，证明它会翻转
# ---------------------------------------------------------------------------
def selftest(fixture: dict, texts: dict, keys: dict, arms: dict) -> list:
    m = fixture["markers"]
    cases = []

    def case(name, got, want):
        cases.append({"control": name, "got": got, "want": want, "ok": got == want})

    verdict_real, _ = judge(fixture, texts, keys, arms)
    case("S1 真读数判出 H_card", verdict_real == "H_card", True)

    # 反向对照：把 overview 渲染当成"工具返回体"喂进同一把判据 ⇒ 必须不再是 H_card
    fake = dict(texts)
    fake["tool_fetch"] = texts["overview_tight_budget"]
    v_over, _ = judge(fixture, fake, keys, arms)
    case("S2 overview 文本冒充时判据翻脸", v_over != "H_card", True)

    # 反向对照：把卡正文替换成 overview 模板句 ⇒ 必须不是 H_card
    fake2 = dict(texts)
    fake2["tool_fetch"] = "%s\n%s\n%s\n%s" % (
        "# Project knowledge overview",
        fixture["card"]["title"],
        OVERVIEW_TEMPLATE_NEEDLE,
        m["repository_only_title"])
    v_fake, _ = judge(fixture, fake2, keys, arms)
    case("S3 清单式冒充时判据翻脸", v_fake != "H_card", True)

    # 针的可开火性：末行针至少在一个臂里真出现过（否则"缺席"毫无意义）
    case("S4 末行针在场", m["tail"] in (texts["tool_fetch"] or ""), True)
    # 分岔的可开火性：小预算 overview 必须真丢正文行，而工具臂一行都不丢
    case("S4b 丢行分岔在场",
         len(body_dropped(texts["overview_tight_budget"] or "", fixture)) >= MIN_DROPPED_BODY_LINES
         and len(body_dropped(texts["tool_fetch"] or "", fixture)) == 0, True)
    # 仓库级针在场：它在 overview 默认预算里出现，所以它在工具返回体里的缺席是可判的
    case("S5 仓库级针在场", m["repository_only_title"] in (texts["overview_default_budget"] or ""), True)
    # 空正文臂不得带针（判据不是靠常量骗绿）
    case("S6 空正文臂无针泄漏", m["tail"] not in (texts["control_empty_body"] or ""), True)
    # 小预算 overview 必须被截断（预算真的生效，而不是喂了空语料）
    case("S7 overview 被预算截断", "showing " in (texts["overview_tight_budget"] or ""), True)
    # 默认预算 overview 必须保住末行（证明 S1 的"末行在场"不是截断器帮忙）
    case("S8 默认预算 overview 保住末行", m["tail"] in (texts["overview_default_budget"] or ""), True)
    return cases


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
def run(project: Path, bundle_override: str | None, keep_scratch: bool) -> int:
    bundle, how = find_bundle(bundle_override)
    if bundle is None:
        print(json.dumps({"ok": False, "errorCode": "BUNDLE_NOT_FOUND", "detail": how}, ensure_ascii=False))
        return 2
    node = shutil.which("node")
    if not node:
        print(json.dumps({"ok": False, "errorCode": "NODE_MISSING", "detail": "node not on PATH"},
                         ensure_ascii=False))
        return 2

    scratch = Path(keep_scratch or tempfile.mkdtemp(prefix="kprobe-"))
    scratch.mkdir(parents=True, exist_ok=True)
    nonce = hashlib.sha1(("%s%s" % (time.time(), os.getpid())).encode()).hexdigest()[:10]
    fixture = build_fixture(nonce)
    fx_path = scratch / "fixture.json"
    out_path = scratch / "outputs.json"
    fx_path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")

    cmd = [node, str(HARNESS), "--bundle", str(bundle), "--fixture", str(fx_path),
           "--scratch", str(scratch), "--out", str(out_path)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0 or not out_path.is_file():
        print(json.dumps({"ok": False, "errorCode": "HARNESS_FAILED", "rc": p.returncode,
                          "stdout": p.stdout[-2000:], "stderr": p.stderr[-2000:],
                          "cmd": " ".join(cmd)}, ensure_ascii=False, indent=2))
        return 2
    data = json.loads(out_path.read_text(encoding="utf-8"))

    precond = check_preconditions(fixture, data["texts"], data["arms"])
    precond_ok = all(p[1] for p in precond)
    verdict, rows = judge(fixture, data["texts"], data["info_key_census"], data["arms"])
    controls = selftest(fixture, data["texts"], data["info_key_census"], data["arms"])
    controls_ok = all(c["ok"] for c in controls)

    report = {
        "question": "召回工具返回的 info 是知识卡本身的内容，还是 overview 区那些卡的清单/摘要",
        "alias_evidence": alias_readouts(project),
        "bundle": {"path": str(bundle), "version_dir": bundle.parent.parent.name,
                   "sha256": data["bundle"]["sha256"], "bytes": data["bundle"]["bytes"],
                   "located_by": how, "patched_copy": str(scratch / "bundle.probe-copy.mjs"),
                   "entry_anchor_hits": data["bundle"]["entry_anchor_hits"]},
        "shipped_symbols_executed": data["symbols"],
        "field_names": data["info_key_census"],
        "constants": data["overview_template_constants"],
        "preconditions": [{"name": n, "holds": bool(h), "reading": r} for n, h, r in precond],
        "preconditions_ok": precond_ok,
        "verdict": verdict if precond_ok else "precondition_failed",
        "observations": rows,
        "controls": controls,
        "controls_ok": controls_ok,
        "fixture": {"nonce": nonce, "card_title": fixture["card"]["title"],
                    "content_lines": len(fixture["card_content"].splitlines()),
                    "content_chars": len(fixture["card_content"]),
                    "overview_max_length": fixture["overview_max_length"],
                    "overview_template_chars": data["overview_section_template_length"]},
        "readings": {
            "tool_fetch_len": len(data["texts"]["tool_fetch"] or ""),
            "tool_search_len": len(data["texts"]["tool_search"] or ""),
            "overview_tight_len": len(data["texts"]["overview_tight_budget"] or ""),
            "overview_default_len": len(data["texts"]["overview_default_budget"] or ""),
            "card_body_len": len(fixture["card_content"]),
        },
        "scratch": str(scratch),
    }
    (scratch / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not precond_ok:
        return 2
    if not keep_scratch:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0 if (verdict == "H_card" and controls_ok) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", default=str(HERE.parent))
    ap.add_argument("--bundle", default=None, help="显式指定 qoder-search.bundle.mjs 路径")
    ap.add_argument("--keep-scratch", default=None,
                    help="把中间件落到指定目录（默认临时目录，跑完删）")
    args = ap.parse_args()
    return run(Path(args.project), args.bundle, args.keep_scratch)


if __name__ == "__main__":
    sys.exit(main())
