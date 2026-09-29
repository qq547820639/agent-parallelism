#!/usr/bin/env node
/**
 * knowledge_info_source_harness.mjs — runs the SHIPPED knowledge-retrieval code
 * of this host (the `qoder-context` plugin bundle) on a synthetic fixture, so the
 * question "what exactly does the recall tool hand back to the model?" is answered
 * by the authority implementation instead of by a re-implementation.
 *
 * It does NOT re-implement any serialization: it loads a byte-patched copy of the
 * installed bundle (only the module's single CLI entry statement is neutralized)
 * and calls the real functions:
 *   QJt  = the MCP tool registration for `SearchKnowledge` (what the host returns)
 *   cU   = the retrieval entry (mode dispatch: search / fetch)
 *   NKe  = the card store (row -> hit mapping, module/architecture expansion)
 *   ZJt  = one card row -> one hit object
 *   PKe  = hits -> the text the model actually receives
 *   Ate  = the `# Project knowledge overview` context injector (separate path)
 *   UKe/FKe/xv = overview tree render / budget truncation / section template
 *
 * Usage:
 *   node knowledge_info_source_harness.mjs --bundle <bundle.mjs> \
 *        --fixture <fixture.json> --scratch <dir> --out <out.json>
 *
 * Exit codes: 0 = arms executed; 2 = precondition failed (bundle/anchor/API drift).
 * Never guesses: every missing expected shape is a hard error with a code.
 */

import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const ENTRY_ANCHOR =
  "process.exitCode=await v$n(_$n(process.argv.slice(2)));";

// Internals the probe drives. Every name is a top-level binding in the bundle;
// the list is asserted at load time (see `take`).
const NEEDED = [
  "QJt", "cU", "NKe", "ZJt", "PKe", "KJt", "xf", "Ate", "UKe", "FKe",
  "xv", "wC", "qJt", "BJt", "Imn", "Sv", "WKe",
  // overview assembly stage (cQ's pipeline): per-card 500-char prefix + tree + budget
  "den", "fen", "men", "pen", "hen", "uen", "len", "cen",
];

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

function fail(code, detail) {
  process.stdout.write(JSON.stringify({ ok: false, errorCode: code, detail }, null, 2) + "\n");
  process.exit(2);
}

const bundlePath = arg("bundle", "");
const fixturePath = arg("fixture", "");
const scratchDir = arg("scratch", "");
const outPath = arg("out", "");
if (!bundlePath || !fixturePath || !scratchDir || !outPath) fail("ARGS_MISSING", "need --bundle --fixture --scratch --out");

let bundleSrc;
try {
  bundleSrc = await readFile(bundlePath, "utf8");
} catch (e) {
  fail("BUNDLE_UNREADABLE", String(e));
}
const fixture = JSON.parse(await readFile(fixturePath, "utf8"));

// The copy is patched in exactly one place: the module's CLI entry statement is
// replaced by a symbol hand-off. Everything else is byte-identical to what the
// host ships, so the serializer/branch logic under test is the real one.
const anchorHits = bundleSrc.split(ENTRY_ANCHOR).length - 1;
if (anchorHits !== 1) fail("ENTRY_ANCHOR_NOT_UNIQUE", `hits=${anchorHits}`);
const patched = bundleSrc.replace(
  ENTRY_ANCHOR,
  `globalThis.__KC = {${NEEDED.join(",")}};`,
);
if (patched === bundleSrc) fail("PATCH_NOOP", "entry statement not replaced");

await mkdir(scratchDir, { recursive: true });
const copyPath = path.join(scratchDir, "bundle.probe-copy.mjs");
await writeFile(copyPath, patched, "utf8");

try {
  await import(pathToFileURL(copyPath).href);
} catch (e) {
  fail("IMPORT_COPY_FAILED", String(e));
}
const KC = globalThis.__KC;
if (!KC) fail("NO_SYMBOL_HANDOFF", "globalThis.__KC absent");
const missing = NEEDED.filter((n) => KC[n] === undefined);
if (missing.length) fail("API_DRIFT", `missing: ${missing.join(",")}`);

const WS = "/knowledge-probe/ws";
const DATA = "/knowledge-probe/data";
const noop = () => {};
const logger = { debug: noop, info: noop, warn: noop, error: noop, child: () => logger };

// ---------------------------------------------------------------------------
// Arm driver: push a fixture through the real tool handler and read what the
// model would receive. `store` is the injected card-store seam.
// ---------------------------------------------------------------------------
async function runToolHandler(mode, store) {
  let handler = null;
  const fakeMcp = {
    registerTool: (name, def, h) => { handler = { name, def, h }; },
  };
  KC.QJt({
    mcp: fakeMcp,
    auth: { getCredential: () => ({ source: "mcp", accessToken: "probe", principal: { uid: "probe" } }) },
    logger,
    resolveWorkspaceRoots: async () => [WS],
    createEntry: (cfg) => KC.cU({ workspaceRoots: cfg.workspaceRoots, logger, ...store }),
  });
  if (!handler) fail("HANDLER_NOT_REGISTERED", "registerTool never called");
  const args = { query: mode === "fetch" ? fixture.fetch_query : fixture.search_query, mode };
  if (fixture.max_results !== undefined) args.max_results = fixture.max_results;
  const result = await handler.h(args, { mcpReq: { _meta: KC.WKe("probe-auth-context") } });
  return {
    handler_name: handler.name,
    result_top_keys: Object.keys(result),
    result_content_shape: (result.content || []).map((c) => ({ type: c.type, keys: Object.keys(c) })),
    text: result.content && result.content[0] ? result.content[0].text : null,
    isError: result.isError === true,
  };
}

async function runEntry(mode, store) {
  const entry = KC.cU({ workspaceRoots: [WS], logger, ...store });
  const res = await entry.search({ query: mode === "fetch" ? fixture.fetch_query : fixture.search_query, mode });
  return {
    result_keys: Object.keys(res),
    count: res.count,
    message: res.message,
    implemented: res.implemented,
    hit_keys: (res.hits || [])[0] ? Object.keys(res.hits[0]) : [],
    hit_titles: (res.hits || []).map((h) => h.title),
    hit_types: (res.hits || []).map((h) => h.type),
    hit_content_lengths: (res.hits || []).map((h) => (typeof h.content === "string" ? h.content.length : null)),
    content_field_length: typeof res.content === "string" ? res.content.length : null,
    serialized_text: res.content,
  };
}

// Row objects shaped like what the sqlite card store hands the bundle.
function row(id, title, content, cardType, opts = {}) {
  return {
    cardId: id,
    title,
    content,
    cardType,
    links: opts.links || [],
    ...(opts.moduleId === undefined ? {} : { moduleId: opts.moduleId }),
    ...(opts.parentTitle === undefined ? {} : { parentTitle: opts.parentTitle }),
    ...(opts.moduleScopes === undefined ? {} : { moduleScopes: opts.moduleScopes }),
  };
}

const CARD = row(fixture.card.id, fixture.card.title, fixture.card.content, fixture.card.cardType, {
  moduleId: fixture.card.moduleId,
  links: fixture.card.links,
  moduleScopes: fixture.card.moduleScopes,
});
const ARCH = row(fixture.arch.id, fixture.arch.title, fixture.arch.content, fixture.arch.cardType, {
  moduleId: fixture.arch.moduleId,
});
const EMPTY_CARD = row(fixture.card.id, fixture.card.title, "", fixture.card.cardType, {
  moduleId: fixture.card.moduleId, links: [],
});

const out = {
  ok: true,
  bundle: {
    path: bundlePath,
    sha256: createHash("sha256").update(bundleSrc).digest("hex"),
    bytes: Buffer.byteLength(bundleSrc),
    entry_anchor_hits: anchorHits,
  },
  symbols: NEEDED.map((n) => `${n}:${typeof KC[n]}`),
  // Field-name census: does the shipped payload carry a literal `info` key?
  info_key_census: {
    in_hit_builder: Object.keys(KC.ZJt(CARD, undefined, 0.5)),
    in_tool_result: Object.keys(KC.KJt(fixture.fetch_query, "fetch", [KC.ZJt(CARD, undefined, 0.5)], undefined)),
    in_handler_result: null, // filled below
    literal_info_found: false,
  },
  overview_section_template: KC.xv,
  overview_section_template_length: KC.xv.length,
  overview_template_constants: { header: KC.wC, tip_line: KC.Imn, no_knowledge_message: KC.qJt },
  arms: {},
};

// --- fetch through the real handler (store seam = fetchByTitles) -----------
out.arms.tool_fetch = await runToolHandler("fetch", {
  cardStore: { fetchByTitles: async () => ({
    databaseAvailable: true,
    hits: [KC.ZJt(CARD, undefined, undefined)],
  }) },
});
out.info_key_census.in_handler_result = out.arms.tool_fetch.result_top_keys;
out.info_key_census.hit_result_content_shape = out.arms.tool_fetch.result_content_shape;
out.info_key_census.literal_info_found =
  out.info_key_census.in_hit_builder.includes("info") ||
  out.info_key_census.in_tool_result.includes("info") ||
  out.arms.tool_fetch.result_top_keys.includes("info");

// --- fetch with NO knowledge database at all (negative arm) ----------------
out.arms.tool_fetch_no_db = await runToolHandler("fetch", {
  cardStore: { fetchByTitles: async () => ({ databaseAvailable: false, hits: [] }) },
});

// --- search through the real handler (store seam = searchByQuery) ----------
out.arms.tool_search = await runToolHandler("search", {
  cardStore: { searchByQuery: async () => ({
    hits: [KC.ZJt(CARD, undefined, 0.9)],
    databaseAvailable: true,
    semanticIndexAvailable: true,
    retrievalFailed: false,
  }) },
});

// --- entry-level shapes (no handler): object the host serializes -----------
out.arms.entry_fetch = await runEntry("fetch", {
  cardStore: { fetchByTitles: async () => ({ databaseAvailable: true, hits: [KC.ZJt(CARD, undefined, undefined)] }) },
});

// --- whole real store layer: rows -> Omn/UJt module expansion -> hits ------
// Exercises the shipped row->hit mapping and the "module title also returns the
// architecture card" expansion, not just the text formatter.
out.arms.store_fetch_via_NKe = await (async () => {
  const store = KC.NKe({
    workspaceRoots: [WS],
    logger,
    resolveDataDirectory: async () => DATA,
    readHostFlags: () => ({ citation: true }),
    fetchCards: async () => ({ databaseExists: true, selection: { partition: "p", snapshotId: "s" }, cards: [CARD] }),
    fetchModuleCards: async () => ({ cards: [ARCH] }),
  });
  const entry = KC.cU({ workspaceRoots: [WS], logger, cardStore: store });
  const res = await entry.search({ query: fixture.fetch_query, mode: "fetch" });
  return {
    hit_titles: res.hits.map((h) => h.title),
    hit_types: res.hits.map((h) => h.type),
    hit_content_is_full_card_body: res.hits.map((h) => h.content === fixture.card.content || h.content === fixture.arch.content),
    content_field_length: res.content.length,
    serialized_text: res.content,
  };
})();

// --- control: empty card body (proves body markers are not template noise) --
out.arms.control_empty_body = await runToolHandler("fetch", {
  cardStore: { fetchByTitles: async () => ({ databaseAvailable: true, hits: [KC.ZJt(EMPTY_CARD, undefined, undefined)] }) },
});

// --- the OTHER path: the `# Project knowledge overview` context injector ----
// The overview's per-module summary is produced by the REAL pipeline
// readCardOutlines -> den -> fen(hen = card.content prefix cut) -> men -> pen;
// only the sqlite handle itself is faked (readCardsByIds returns the fixture rows).
function fakeOutlineStore(cards, modules) {
  return {
    readCardOutlines: () => cards,
    readModuleTree: () => modules,
    readCardsByIds: (pid, ids, snap) =>
      cards.filter((c) => ids.includes(c.id)).map((c) => ({ card: { id: c.id, content: c.content } })),
    close: () => {},
  };
}

function overviewViaRealPipeline(fixtureCards) {
  const modules = [{ id: "mod-" + fixture.nonce, parentId: "", ordinal: 0, path: "checks", title: "probe module", meta: {} }];
  // 卡片行的主键在 bundle 里叫 cardId（ZJt 读 e.cardId），readCardOutlines 的行主键叫 id
  const outlines = fixtureCards.map((c, k) => ({
    id: c.cardId !== undefined ? c.cardId : c.id,
    cardType: c.cardType, title: c.title, content: c.content,
    moduleId: c.moduleId === undefined ? "" : c.moduleId, ordinal: k,
  }));
  // 仓库级条目（Su 白名单里的类型）：pen() 只取 {cardType,title}，正文根本不进 overview
  outlines.push({
    id: "repo-" + fixture.nonce, cardType: "build_system",
    title: fixture.repository_card_title,
    content: "REPO-BODY-" + fixture.nonce + " 仓库级卡正文，overview 里只有它的标题。",
    moduleId: "", ordinal: outlines.length,
  });
  const store = fakeOutlineStore(outlines, modules);
  const byModule = KC.den(outlines);
  const contentByCard = KC.fen(store, "probe-partition", "probe-snapshot", modules, byModule, {});
  const roots = KC.men(modules, byModule, contentByCard);
  const repositoryCards = KC.pen(outlines);
  return {
    roots,
    repositoryCards,
    hen_cut: outlines.map((c) => KC.hen(c.content.trim(), KC.len)),
    per_card_cut_limit: KC.len,
    content_threshold_modules: KC.uen,
  };
}

const pipeline = overviewViaRealPipeline([CARD, ARCH]);
out.arms.overview_pipeline = {
  roots_title: pipeline.roots[0] && pipeline.roots[0].title,
  roots_content_present: pipeline.roots[0] && pipeline.roots[0].content !== undefined,
  roots_content_len: pipeline.roots[0] && (pipeline.roots[0].content || "").length,
  roots_children: pipeline.roots[0] ? pipeline.roots[0].children.length : null,
  repository_cards: pipeline.repositoryCards,
  repository_cards_carry_content: pipeline.repositoryCards.some((c) => c.content !== undefined),
  per_card_cut_limit: pipeline.per_card_cut_limit,
  content_threshold_modules: pipeline.content_threshold_modules,
  long_card: {
    cut_length: pipeline.hen_cut[0].length,
    keeps_head: pipeline.hen_cut[0].includes(fixture.markers.head),
    keeps_tail: pipeline.hen_cut[0].includes(fixture.markers.tail),
    ends_with_ellipsis: pipeline.hen_cut[0].endsWith("..."),
    cut_is_prefix_of_body: fixture.card_content.startsWith(pipeline.hen_cut[0].slice(0, -3)),
  },
  short_card: {
    cut_length: pipeline.hen_cut[1].length,
    unchanged_by_cut: pipeline.hen_cut[1] === fixture.arch.content,
    ends_with_ellipsis: pipeline.hen_cut[1].endsWith("..."),
  },
};
// rendered with the same budget Ate uses at its default maxLength (6000)
const textPipelineOverview = KC.FKe(
  KC.UKe({ roots: pipeline.roots, repositoryCards: pipeline.repositoryCards }),
  6000 - KC.xv.length - 2,
);
// > threshold modules: fen() returns an empty Map, so the tree carries no summaries at all
const manyModules = Array.from({ length: 31 }, (_, k) => ({
  id: "mod-" + k, parentId: "", ordinal: k, path: "m" + k, title: "Module " + k, meta: {},
}));
const manyOutlines = manyModules.map((m) => ({
  id: "c-" + m.id, cardType: "overview", title: m.title, content: fixture.card_content, moduleId: m.id, ordinal: m.ordinal,
}));
const manyStore = fakeOutlineStore(manyOutlines, manyModules);
const manyByModule = KC.den(manyOutlines);
const manyContent = KC.fen(manyStore, "p", "snap", manyModules, manyByModule, {});
out.arms.overview_above_threshold = {
  module_count: manyModules.length,
  content_map_size: manyContent.size,
  fen_returns_empty: manyContent.size === 0,
};
const textAboveThreshold = KC.UKe({
  roots: KC.men(manyModules, manyByModule, manyContent), repositoryCards: [],
});

// --- the OTHER path: the `# Project knowledge overview` context injector ----
async function renderOverview(maxLength) {
  const a = KC.Ate({
    workspaceRoot: WS,
    logger,
    maxLength,
    resolveDataDirectory: async () => DATA,
    readHostFlags: () => ({ citation: true }),
    readOverview: async () => ({
      databaseExists: true,
      moduleCount: 1,
      roots: [{ title: fixture.card.title, content: fixture.card.content, children: [] }],
      repositoryCards: [{ title: fixture.repository_card_title, content: undefined }],
    }),
  });
  return await a.render();
}
out.arms.overview_tight_budget = await renderOverview(fixture.overview_max_length);
out.arms.overview_default_budget = await renderOverview(undefined);
out.arms.overview_max_length_used = fixture.overview_max_length;

// --- direct serializer readouts (raw texts for the Python criterion) -------
out.texts = {
  tool_fetch: out.arms.tool_fetch.text,
  tool_search: out.arms.tool_search.text,
  tool_fetch_no_db: out.arms.tool_fetch_no_db.text,
  control_empty_body: out.arms.control_empty_body.text,
  overview_tight_budget: out.arms.overview_tight_budget,
  overview_default_budget: out.arms.overview_default_budget,
  store_fetch_via_NKe: out.arms.store_fetch_via_NKe.serialized_text,
  overview_via_pipeline: textPipelineOverview,
  overview_above_threshold: textAboveThreshold,
};

await writeFile(outPath, JSON.stringify(out, null, 2), "utf8");
process.stdout.write(JSON.stringify({ ok: true, out: outPath, sha256: out.bundle.sha256 }) + "\n");
