/**
 * @file renderer.js — 零依赖的 DOM 构建模块（hyperscript 风格函数式 API）
 *
 * 定位：只用浏览器原生 DOM API（createElement / createElementNS / createTextNode /
 * setAttribute / addEventListener）。不引入任何第三方依赖，也不使用 innerHTML ——
 * 字符串一律经 createTextNode 成为文本节点，这是本模块的 XSS 防线。
 *
 * 公开 API：
 *   - {@link createFragment}       递归构建元素树（主入口）
 *   - {@link normalizeElementNode} 把任意值收敛为真实节点，否则抛 TypeError
 *   - {@link validateAttributes}   属性校验（黑名单 + 危险协议），可独立调用
 *   - {@link sanitizeAttributes}   同一条判据的"摘掉不合规项"版本，供未校验的外部数据用
 *   - {@link renderList}           列表 → 子节点数组（含空态 fallback）
 *   - {@link delegate}             事件委托：容器上一个监听器覆盖整棵子树
 *   - {@link delegateEvents}       事件委托：接住 opts.delegate 模式下登记的处理器
 *   - {@link renderToHTML}         节点 → HTML 字符串（SSR / 快照断言 / 调试）
 *   - {@link escapeHTML}           文本与属性值转义
 *
 * 用法示例：
 *   import { createFragment, renderList, delegate } from './renderer.js';
 *
 *   const frag = createFragment('div', { class: 'card' }, ['标题']);
 *
 *   const rows = renderList(
 *     items,
 *     (it) => createFragment('li', { class: 'row', 'data-id': it.id }, [it.name]),
 *     createFragment('p', { class: 'empty' }, ['暂无数据']),
 *   );
 *   const card = createFragment('section', { class: 'list' }, [rows]);
 *
 *   // 一个监听器覆盖所有行；点单元格里的 span 时 realTarget 仍是那一行 li
 *   const off = delegate(card, 'li[data-id]', 'click',
 *     (ev, { realTarget }) => pick(realTarget.dataset.id));
 *
 * 自测（不需要浏览器，自带 DOM 桩件与开火控制）：
 *   node src/renderer.js --selftest
 *   退出码三档，别把 2 读成 1：0 = 全过；1 = 判据红（有控制按名失败）；
 *   2 = 尺子自己坏了（某条断言抛异常，分母不可信，输出末行为 RESULT: ABORTED）。
 *   注：入口判定比较 process.argv[1] 与 import.meta.filename，在符号链接目录下
 *   （macOS 的 /tmp → /private/tmp）用软链路径直接运行会静默不执行，请给真实路径。
 *
 * 借鉴语义而未依赖的成熟实现（版本号 / License / 最后发布日均取自 npm registry 一手读数，
 * 2026-09-27 实测；`picdom`、`decomodel` 在 registry 返回 404，未检索到）：
 *   - hyperscript@2.0.2（MIT，最后发布 2016-08-25，3 个运行时依赖）：借它
 *     `h(tag, attrs, children)` 的三参数接口形状，以及"children 里的 null/false 视为空槽位"
 *     这一条件渲染约定。已停维护且依赖虚拟 DOM，不引。
 *   - nanohtml@1.10.0（MIT，最后发布 2022-04-21，13 个依赖含 acorn-node / transform-ast）：
 *     借它"字符串永不进 innerHTML"的安全模型。它是 tagged template + 构建期 AST 改写，
 *     需要打包器，不引。
 *   - delegated-events@1.1.2（MIT，最后发布 2020-04-03，依赖 selector-set）：借它
 *     `closest` + 单监听器的委托骨架，和"返回一个反注册函数"的接口约定。
 *   - lit-html@3.3.3（BSD-3-Clause，最后发布 2026-05-14）：唯一仍在活跃维护的候选，但它是
 *     模板结果引擎（TemplateResult + 自己的脏检查），与"函数式直出真实节点"目标不合，
 *     且会把依赖带进运行时，不引。
 * 结论：自研 + 借上述接口形状与实现思路。与 scripts/fit_kappa.py 对 USL 拟合器的处置一致。
 */

/** HTML 命名空间（绝大多数元素走这个默认值，无需显式传入）。 */
export const HTML_NS = 'http://www.w3.org/1999/xhtml';
/** SVG 命名空间。 */
export const SVG_NS = 'http://www.w3.org/2000/svg';
/** MathML 命名空间。 */
export const MATHML_NS = 'http://www.w3.org/1998/Math/MathML';

/**
 * 只存在于 SVG 的局部名。命中即自动用 SVG_NS 创建，避免"在 svg 之外误写 circle"时
 * 静默产出一个 HTML 未知元素。**共享名（a / title / style / text / script 等）刻意不列入**，
 * 一律按 HTML 处理：需要 SVG 侧时用第 4 个参数显式给 namespace，或用描述对象形式让
 * 命名空间沿子树继承（见 {@link createFragment} 的 opts 说明）。
 * @type {ReadonlySet<string>}
 */
export const SVG_LOCAL_TAGS = Object.freeze(new Set([
  'svg', 'circle', 'ellipse', 'line', 'path', 'polygon', 'polyline', 'rect',
  'g', 'defs', 'use', 'symbol', 'mask', 'clippath', 'pattern', 'marker',
  'lineargradient', 'radialgradient', 'stop', 'filter', 'fegaussianblur',
  'feoffset', 'feblend', 'fecomposite', 'feturbulence', 'fedropshadow',
  'foreignobject', 'image', 'textpath', 'tspan', 'animate', 'animatetransform',
  'animatemotion', 'view', 'switch', 'meshgradient',
]));

/**
 * 会被浏览器当作 URL 解析的属性，值要过危险协议检查。
 * 注意 `data`（`<object data=…>`）在这里，而 `data-*` 前缀不在 —— 两者不是一回事。
 * @type {ReadonlySet<string>}
 */
export const URL_ATTRIBUTES = Object.freeze(new Set([
  'href', 'src', 'srcset', 'action', 'formaction', 'poster', 'background',
  'data', 'cite', 'ping', 'longdesc', 'manifest', 'usemap', 'xlink:href', 'xmlns',
]));

/** 危险协议前缀（含各种空白/控制字符变形）。@type {RegExp} */
const DANGEROUS_PROTOCOL_RE = /^(?:javascript|vbscript|livescript|mocha|data)\s*:/i;
/** URL 判定前先剥掉的空白与控制字符（浏览器解析 URL 时会忽略它们）。@type {RegExp} */
const CONTROL_CHARS_RE = /[\s\u0000-\u001f\u007f-\u009f]/g;

/** HTML 布尔属性：序列化时只出名字，不出 `="true"`。 */
const BOOLEAN_ATTRIBUTES = new Set([
  'allowfullscreen', 'async', 'autofocus', 'checked', 'default', 'defer',
  'disabled', 'formnovalidate', 'hidden', 'inert', 'ismap', 'itemscope',
  'multiple', 'muted', 'nomodule', 'novalidate', 'open', 'playsinline',
  'readonly', 'required', 'reversed', 'selected',
]);

/** HTML 空元素（无闭合标签）。 */
const VOID_TAGS = new Set([
  'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
  'meta', 'param', 'source', 'track', 'wbr',
]);

/** 属性名允许的字符集；越界即拒（否则 setAttribute 会静默改名）。 */
const ATTR_NAME_RE = /^[a-z_:][a-z0-9_:.\-]*$/;
/** 标签名的合法形状。 */
const TAG_NAME_RE = /^[A-Za-z][A-Za-z0-9:._-]*$/;
/** 事件属性的两种写法：`onclick` 与 `on-click` / `on:click`。 */
const EVENT_NAME_RE = /^on[:-]?([a-z][a-z0-9]*)$/;

/**
 * 取当前环境的 document。不在浏览器（或尚未加载）时给出可定位的错误，
 * 而不是 `Cannot read properties of undefined`。
 * @returns {Document}
 * @throws {TypeError} 全局 document 不可用时
 */
function requireDocument() {
  const doc = typeof globalThis !== 'undefined' ? globalThis.document : undefined;
  if (!doc || typeof doc.createElement !== 'function') {
    throw new TypeError(
      'renderer.js 需要一个全局 document（浏览器环境，或测试环境注入的 DOM 实现）。'
      + '当前 globalThis.document 不可用。',
    );
  }
  return doc;
}

/**
 * 内部工具：把任意值转成便于人读的类型名，用于错误信息。
 * @param {unknown} v
 * @returns {string}
 */
function describe(v) {
  if (v === null) return 'null';
  if (Array.isArray(v)) return 'array';
  if (typeof v === 'object') {
    const ctor = /** @type {any} */ (v).constructor;
    return ctor && ctor.name ? `${ctor.name}(object)` : 'object';
  }
  return typeof v;
}

/**
 * 把驼峰写法的属性名折成 HTML 的小写形式。
 *
 * HTML 属性本就大小写不敏感，setAttribute 会静默把 `tabIndex` 变成 `tabindex`。
 * 本模块把这一步显式化，好让 {@link validateAttributes} 不会因为 `OnClick` 这种写法
 * 绕过 `on*` 黑名单 —— 大小写敏感的黑名单是最常见的假合规。
 * 已经是小写的名字直接返回，不重复分配字符串。
 *
 * @param {string} key 原始属性名（不做 trim：名字里的空白会在 {@link validateAttributes} 被拒）
 * @returns {string} 小写后的属性名
 */
export function normalizeAttrKey(key) {
  return key === key.toLowerCase() ? key : key.toLowerCase();
}

/**
 * 内部工具：判定属性名是否属于"事件类属性"。
 *
 * 按规格要求这条判定是**前缀式黑名单**：`on` 开头即算事件类（`onclick`、`onmouseover`、
 * `on-click`、`on:click` 全中），值必须是函数。代价是 `only`、`once` 这类恰好以 on 开头
 * 的自定义属性也得写成函数或换个名字 —— 它是**响亮的报错**而不是静默放行，所以取严。
 * `data-*` / `aria-*` 前缀优先豁免，于是 `data-onclick` 不会被当成事件绑定。
 *
 * @param {string} lowerKey 已归一小写的属性名
 * @returns {boolean}
 */
function isEventAttrKey(lowerKey) {
  return !lowerKey.startsWith('data-') && !lowerKey.startsWith('aria-')
    && lowerKey.startsWith('on');
}

/**
 * 内部工具：从事件类属性名取出事件类型名。
 * `onclick` / `OnClick` → `click`；`on-click` / `on:click` → `click`；
 * `onmouseover` → `mouseover`。
 * @param {string} lowerKey
 * @returns {string}
 */
function eventNameOf(lowerKey) {
  const m = EVENT_NAME_RE.exec(lowerKey);
  if (!m) return lowerKey.slice(2);
  return m[1];
}

/**
 * 内部工具：`javascript:` 及同类危险协议的判定。
 * 先剥掉空白与控制字符（浏览器解析 URL 时会忽略它们）再比协议，于是
 * `java\tscript:`、` JavaScript:` 这类变形同样被拒；`about:blank` 放行。
 * `blob:` 与 `data:` 分开判：`data:` 在 URL 属性里一律拒（含 base64 HTML），
 * `blob:` 需要应用自己保证来源，故不在此拒。
 * @param {string} value
 * @returns {boolean}
 */
function isDangerousUrl(value) {
  const squeezed = value.replace(CONTROL_CHARS_RE, '').toLowerCase();
  if (/^(?:javascript|vbscript|livescript|mocha|data):/.test(squeezeProbe(squeezed))) {
    return true;
  }
  // 原文也判一次：剥空白后恰好不再像协议（例如夹了零宽字符）时不能漏网。
  return DANGEROUS_PROTOCOL_RE.test(value.trim());
}

/** 内部工具：协议冒号前可能有任意前缀（相对 URL 里没有冒号时原样返回）。 */
function squeezeProbe(s) {
  const colon = s.indexOf(':');
  return colon < 0 ? s : s.slice(0, colon + 1);
}

/**
 * 判断值是否宿主环境真实造出来的 DOM 节点（元素 / 文本 / 注释 / DocumentFragment）。
 *
 * 做法是把候选值交给宿主 `Node.prototype` 上的 `nodeType` 访问器去读 —— 也就是走
 * 浏览器实现的 brand check，而不是自己猜字段。于是"手拼一个 `{nodeType:1, tagName:'IMG'}`"
 * 会被拒（`Node.prototype.nodeType` 收到非 Node 的 this 会抛，捕获后判 false），
 * "跨 realm 的真实 Text 节点"会被放。宿主不提供 `Node` 时退化为字段探测，
 * 该退化分支由自测的 C2h 单独钉住。
 *
 * @param {unknown} v
 * @returns {boolean}
 */
export function isDomNode(v) {
  if (v === null || (typeof v !== 'object' && typeof v !== 'function')) return false;
  const NodeCtor = typeof globalThis !== 'undefined' ? /** @type {any} */ (globalThis).Node : undefined;
  const proto = NodeCtor ? NodeCtor.prototype : undefined;
  if (proto) {
    try {
      const desc = Object.getOwnPropertyDescriptor(proto, 'nodeType');
      if (desc && typeof desc.get === 'function') {
        return [1, 3, 8, 11].includes(desc.get.call(v));
      }
      return [1, 3, 8, 11].includes(/** @type {any} */ (v).nodeType);
    } catch {
      return false; // getter 因 this 不是 Node 而抛 —— 正是要拒的那种
    }
  }
  return [1, 3, 8, 11].includes(/** @type {any} */ (v).nodeType);
}

/**
 * 内部工具：值是否是"元素描述对象"（带合法 tagName 的纯对象）。
 * @param {any} v
 * @returns {boolean}
 */
function isDescriptor(v) {
  return !!v && typeof v === 'object' && typeof v.tagName === 'string'
    && TAG_NAME_RE.test(v.tagName.trim());
}

/**
 * 把任意值收敛为一个可挂载的 DOM 节点，不合规就抛 TypeError（不静默强转）。
 *
 * 接受的输入：
 *  1. 真实 DOM 节点（由 {@link isDomNode} 用宿主实现验证）—— **原样返回，不重复创建**；
 *  2. 元素描述对象：`{tagName: 'img', attrs: {src: '…'}, children: [...]}`，
 *     也接受把属性直接摊在顶层的简写 `{tagName: 'img', src: '…'}`；两者都递归走
 *     {@link createFragment} 展开；
 *  3. `null` / `undefined` → 抛错（空槽位是 children 的约定，不是节点的取值）。
 *
 * 字符串**不接受**：文本节点必须由 {@link createFragment} 显式 createTextNode，
 * 任何"把字符串当成一段 HTML"的路径都是 XSS 入口。
 *
 * @param {unknown} node 待归一化的值
 * @param {{namespace?: string, delegate?: boolean}} [opts] 交给 {@link createFragment} 的构建选项
 *        （描述对象在父树里展开时，命名空间与委托模式沿此继承）
 * @returns {Element|Text|Comment} 已验证或已展开的节点
 * @throws {TypeError} node 为 null/undefined、字符串，或既不是真实节点也不是描述对象
 *         （错误信息含实际类型与形状）
 */
export function normalizeElementNode(node, opts = {}) {
  if (node === null || node === undefined) {
    throw new TypeError(
      `normalizeElementNode 需要一个 DOM 节点或元素描述对象，实际收到 ${node} (${describe(node)})。`
      + ' 文本请作为 createFragment 的 children 元素；节点缺失请从 children 里省略。',
    );
  }
  if (isDomNode(node)) {
    return /** @type {Element|Text|Comment} */ (node);
  }
  if (typeof node === 'string') {
    throw new TypeError(
      'normalizeElementNode 不接受字符串。字符串只允许出现在 createFragment 的 children 里，'
      + '那里会走 createTextNode（防 XSS）；实际收到：'
      + JSON.stringify(node.length > 40 ? `${node.slice(0, 40)}…` : node),
    );
  }
  if (isDescriptor(node)) {
    const desc = /** @type {any} */ (node);
    const tag = String(desc.tagName).trim();
    /** @type {Record<string, unknown>} */
    const attrs = { ...(desc.attrs || {}) };
    for (const [k, v] of Object.entries(desc)) {
      if (k === 'tagName' || k === 'attrs' || k === 'children') continue;
      if (typeof v === 'function' || typeof v === 'string'
        || typeof v === 'number' || v === null || v === undefined) {
        attrs[k] = v; // 顶层简写属性：{tagName:'img', src:'/a.png'}
      }
    }
    const children = desc.children === undefined || desc.children === null ? [] : desc.children;
    return createFragment(tag, attrs, children,
      { ...opts, namespace: typeof opts.namespace === 'string' && opts.namespace
        ? opts.namespace : nsOfTag(tag, opts) });
  }
  throw new TypeError(
    `normalizeElementNode 无法把 ${describe(node)} 归一化为 DOM 节点：`
    + ' 它既不是宿主环境造出的节点（nodeType 未通过宿主验证），也不带合法 tagName。'
    + ' 需要文本节点请传字符串给 createFragment 的 children。',
  );
}

/**
 * 内部工具：按属性名与值判断该属性是否安全。**只读，不抛**，
 * 供 {@link validateAttributes} 与 {@link sanitizeAttributes} 共用，保证两处判定同源。
 * @param {string} key 已小写归一的属性名
 * @param {unknown} value
 * @returns {{ok: true} | {ok: false, reason: string}}
 */
function classifyAttr(key, value) {
  if (typeof key !== 'string' || key.length === 0) {
    return { ok: false, reason: '属性名必须是非空字符串' };
  }
  if (!ATTR_NAME_RE.test(key)) {
    return { ok: false, reason: `属性名含非法字符：${JSON.stringify(key)}` };
  }
  if (isEventAttrKey(key)) {
    if (typeof value === 'function') return { ok: true };
    return {
      ok: false,
      reason: `事件属性 ${JSON.stringify(key)} 的值必须是函数（交给事件绑定/委托处理），收到 ${describe(value)}。`
        + ' 字符串形式的内联事件等于 onclick="…"，本模块一律拒绝。',
    };
  }
  if (key === 'style') {
    return { ok: false, reason: 'style 属性被禁用：内联样式绕过样式表约定，且在 '
      + "CSP style-src 'none' 下会静默失效。请改用 class。" };
  }
  if (key === 'srcdoc') {
    return { ok: false, reason: 'srcdoc 属性被禁用：它本身就是一段 HTML。' };
  }
  if (value === null || value === undefined) return { ok: true }; // 空槽位 = 不写这个属性
  if (typeof value !== 'string') {
    return {
      ok: false,
      reason: `属性 ${JSON.stringify(key)} 的值必须是字符串（或事件属性的函数），收到 ${describe(value)}`,
    };
  }
  const isDataOrAria = key.startsWith('data-') || key.startsWith('aria-');
  if (isDataOrAria) return { ok: true }; // 不会被解释为 URL，任意字符串放行
  if (URL_ATTRIBUTES.has(key) && isDangerousUrl(value)) {
    return {
      ok: false,
      reason: `属性 ${JSON.stringify(key)} 的值 ${JSON.stringify(value)} 命中危险协议`
        + '（javascript: / vbscript: / livescript: / mocha: / data:）。',
    };
  }
  return { ok: true };
}

/**
 * 验证属性对象，不合法即抛 TypeError。不写入任何东西，也不改动入参。
 *
 * 拒绝清单：
 *  - `on*` 前缀属性且值不是函数（含 `OnClick` 大小写变体、`onclick: "alert(1)"` 字符串内联事件）；
 *  - `style`、`srcdoc`；
 *  - URL 类属性（href / src / srcset / action / formaction / poster / background / data /
 *    cite / ping / longdesc / manifest / usemap / xlink:href）里出现 `javascript:`、
 *    `vbscript:`、`livescript:`、`mocha:`、`data:` —— 含 `java\tscript:`、` JavaScript:`
 *    这类空白/控制字符/大小写变形；
 *  - 属性名含非法字符（空白、`<`、引号等）；
 *  - 值既不是字符串、也不是函数、也不是 null/undefined 空槽位。
 *
 * 放宽：`data-*` 与 `aria-*` 接受任意字符串值且不参与 URL 协议检查（它们不会被浏览器
 * 解释为 URL 或脚本）。`data-onclick` 因此不会被误当成事件绑定。
 *
 * @param {Record<string, unknown> | null | undefined} attrs 待验证的属性对象
 * @returns {Record<string, unknown>} 通过时原样返回入参（便于链式使用）
 * @throws {TypeError} attrs 不是对象/数组，或任一属性不合法（信息含属性名、实际值与原因）
 */
export function validateAttributes(attrs) {
  if (attrs === null || attrs === undefined) {
    return /** @type {Record<string, unknown>} */ (attrs || {});
  }
  if (typeof attrs !== 'object' || Array.isArray(attrs)) {
    throw new TypeError(
      `attrs 必须是属性对象，收到 ${describe(attrs)}。`
      + ' 把子节点数组当第 2 个参数传是常见笔误，children 是第 3 个参数。',
    );
  }
  for (const [rawKey, value] of Object.entries(/** @type {Record<string, unknown>} */ (attrs))) {
    const verdict = classifyAttr(normalizeAttrKey(rawKey), value);
    if (!verdict.ok) {
      throw new TypeError(`不合法的属性 ${JSON.stringify(rawKey)}：${verdict.reason}`);
    }
  }
  return attrs;
}

/**
 * 把一棵**未经本模块校验**的属性对象（接口返回的配置、用户自定义列的 props）洗一遍，
 * 返回只含合规项的新对象。
 *
 * 与 {@link validateAttributes} 共用同一条 {@link classifyAttr} 判定，**是别名不是第二套规则**：
 * 不深扫 children —— 调用方在 {@link createFragment} 的每个元素上都会再走一次同一判定。
 * 事件类属性（即使值是函数）一律摘掉：这里没有 {@link createFragment} 的包装可用，
 * 摘掉比就地 addEventListener 安全。
 *
 * @param {Record<string, unknown> | null | undefined} obj 任意属性对象（含未校验的外部数据）
 * @returns {Record<string, unknown>} 新对象，键已小写归一，只保留通过判定的项
 * @throws {TypeError} obj 是数组或非对象
 */
export function sanitizeAttributes(obj) {
  /** @type {Record<string, unknown>} */
  const out = {};
  if (obj === null || obj === undefined) return out;
  if (typeof obj !== 'object' || Array.isArray(obj)) {
    throw new TypeError(`sanitizeAttributes 需要一个属性对象，收到 ${describe(obj)}。`);
  }
  for (const [rawKey, value] of Object.entries(/** @type {Record<string, unknown>} */ (obj))) {
    const key = normalizeAttrKey(rawKey);
    if (!classifyAttr(key, value).ok) continue; // 明确不合法 → 不写
    if (isEventAttrKey(key)) continue; // 事件交给 createFragment / delegate
    out[key] = value;
  }
  return out;
}

/**
 * 解析标签名该用哪个命名空间。命名空间沿子树继承，所以显式 opts.namespace 优先。
 * @param {string} tagName
 * @param {{namespace?: string}} [opts]
 * @returns {string|undefined} undefined 表示走 HTML 默认命名空间
 */
function nsOfTag(tagName, opts = {}) {
  if (typeof opts.namespace === 'string' && opts.namespace) return opts.namespace;
  const lower = tagName.trim().toLowerCase();
  if (lower === 'math') return MATHML_NS;
  if (SVG_LOCAL_TAGS.has(lower)) return SVG_NS;
  return undefined;
}

/**
 * 内部工具：把一个值插进父节点。字符串/数字 → 文本节点；节点 → 原样挂；
 * 数组 → 递归摊平；`null`/`undefined`/`true`/`false` → 空槽位跳过。
 * @param {Element} parent
 * @param {unknown} child
 * @param {{namespace?: string, delegate?: boolean}} opts 构建选项（命名空间向下继承）
 */
function insertChild(parent, child, opts) {
  if (child === null || child === undefined || child === false || child === true) {
    return; // hyperscript 系的空槽位约定：`cond && el`
  }
  const doc = requireDocument();
  if (typeof child === 'string') {
    parent.appendChild(doc.createTextNode(child)); // 防 XSS：永不进 innerHTML
    return;
  }
  if (typeof child === 'number') {
    if (!Number.isFinite(child)) {
      throw new TypeError(`children 里的数字必须是有限值，收到 ${String(child)}。`);
    }
    parent.appendChild(doc.createTextNode(String(child)));
    return;
  }
  if (Array.isArray(child)) {
    for (const one of child) insertChild(parent, one, opts);
    return;
  }
  parent.appendChild(normalizeElementNode(child, opts));
}

/**
 * 递归构建一个 DOM 元素（本模块主入口）。
 *
 * @param {string} tagName 标签名，如 `'div'`；`svg`/`circle` 等 SVG 专有名与 `math` 自动走
 *        {@link createElementNS}，其余按 HTML 默认命名空间
 * @param {Record<string, unknown>} [attrs] 属性表。key 统一小写归一；函数值视为事件绑定
 *        （`onclick: fn` 或 `on:click: fn`）；`data-*` / `aria-*` 接受任意字符串；
 *        `style` / `srcdoc` / 字符串内联事件 / 危险协议 URL 一律抛错（见 {@link validateAttributes}）；
 *        `null`/`undefined` 表示不写该属性
 * @param {Array<Element|Text|string|number|boolean|null|undefined|Array<any>|{tagName:string}>} [children]
 *        子节点数组：真实节点**原样挂载**（不重复创建）；字符串与数字经 `createTextNode`
 *        成为文本节点（防 XSS）；数组递归摊平；`null`/`undefined`/`true`/`false` 视为空槽位
 *        跳过（供 `cond && el` 写法）；带 `tagName` 的纯对象作为元素描述对象递归展开；
 *        其余既非节点也非描述对象的值抛 TypeError
 * @param {{namespace?: string, delegate?: boolean}} [opts] `namespace` 显式指定命名空间并沿子树
 *        继承（HTML/SVG 共享名如 `a`、`text`、`title` 需要它）；`delegate: true` 时事件属性
 *        不调用 addEventListener，只记在元素上并打 `data-ev` 标记，由 {@link delegateEvents}
 *        在容器上统一派发
 * @returns {Element} 新建的元素
 * @throws {TypeError} tagName 非非空字符串、attrs 不合法、或 children 里有无法识别的值
 */
export function createFragment(tagName, attrs = {}, children = [], opts = {}) {
  if (typeof tagName !== 'string' || tagName.trim() === '' || !TAG_NAME_RE.test(tagName.trim())) {
    throw new TypeError(
      `createFragment 的第一个参数 tagName 必须是非空标签名字符串，收到 ${describe(tagName)}`
      + `（值：${JSON.stringify(tagName)}）。`,
    );
  }
  const doc = requireDocument();
  const options = opts && typeof opts === 'object' ? opts : {};
  if (options.namespace !== undefined
    && (typeof options.namespace !== 'string' || options.namespace === '')) {
    throw new TypeError(
      `opts.namespace 必须是非空字符串（命名空间 URI），收到 ${describe(options.namespace)}。`,
    );
  }
  const tag = tagName.trim();
  const ns = nsOfTag(tag, options);
  const childOpts = { ...options, namespace: ns }; // 命名空间向下继承

  validateAttributes(attrs);

  const el = ns ? doc.createElementNS(ns, tag) : doc.createElement(tag);

  let className = null;
  let htmlFor = null;
  /** @type {Array<[string, Function]>} */
  const handlers = [];

  for (const [rawKey, rawValue] of Object.entries(/** @type {Record<string, unknown>} */ (attrs || {}))) {
    const key = normalizeAttrKey(rawKey);
    if (rawValue === null || rawValue === undefined) continue; // 空槽位 = 不写
    if (isEventAttrKey(key)) {
      handlers.push([eventNameOf(key), /** @type {Function} */ (rawValue)]);
      continue; // 事件属性不是 HTML 属性，绝不进 setAttribute
    }
    const value = String(rawValue);
    if (key === 'class') {
      className = className === null ? value : `${className} ${value}`;
    } else if (key === 'for') {
      htmlFor = htmlFor === null ? value : `${htmlFor} ${value}`;
    } else {
      el.setAttribute(key, value);
    }
  }
  // class / for 用 setAttribute 而不是 el.className / el.htmlFor：SVG 元素的 className
  // 是只读的 SVGAnimatedString，赋值会静默失败；setAttribute 对两种命名空间都成立，
  // 且浏览器会把它反映到 className / classList 上。
  if (className !== null) el.setAttribute('class', className);
  if (htmlFor !== null) el.setAttribute('for', htmlFor);

  // 先挂子树再处理事件：这样事件属性对已构建的子节点同样有效。
  if (Array.isArray(children)) {
    for (const child of children) insertChild(el, child, childOpts);
  } else {
    insertChild(el, children, childOpts);
  }

  if (options.delegate === true) {
    /** @type {any} */ (el).__rendererEvents = handlers;
    if (handlers.length) el.setAttribute('data-ev', handlers.map(([t]) => t).join(' '));
  } else {
    for (const [type, fn] of handlers) {
      el.addEventListener(type, (ev) => fn(ev, { realTarget: el }));
    }
  }
  return el;
}

/**
 * 列表 → 子节点数组，配合 {@link createFragment} 的 children 使用。
 *
 * @template T
 * @param {T[]|null|undefined} list 数据列表。`null` / `undefined` / 空数组都走 fallback；
 *        其他非数组值（对象、字符串、数字）抛 TypeError，**不静默返回空**
 * @param {(item: T, index: number) => (Element|Text|string|number)} itemRenderer
 *        逐项渲染器，返回值直接进 children（签名保持 (item, index) 不变）
 * @param {unknown} [fallback] 列表为空或未提供时的返回值（例如一个空态节点）
 * @returns {unknown[]} 渲染结果数组；空/未提供列表时为 `[fallback]`，
 *          fallback 为 null/undefined（默认）时为 `[]`，可直接摊进 children
 * @throws {TypeError} list 是非数组且非 null/undefined；或 itemRenderer 不是函数
 */
export function renderList(list, itemRenderer, fallback = null) {
  if (typeof itemRenderer !== 'function') {
    throw new TypeError(`renderList 的 itemRenderer 必须是函数，收到 ${describe(itemRenderer)}。`);
  }
  if (list === null || list === undefined) {
    return fallback === null || fallback === undefined ? [] : [fallback];
  }
  if (!Array.isArray(list)) {
    throw new TypeError(
      `renderList 的 list 必须是数组，收到 ${describe(list)}。`
      + ' 接口返回包装对象时请传 res.items；确实没有数据请传 null 或 []，那会走 fallback。',
    );
  }
  if (list.length === 0) {
    return fallback === null || fallback === undefined ? [] : [fallback];
  }
  return list.map((item, index) => itemRenderer(item, index));
}

/**
 * 事件委托：在容器上装**一个**监听器，覆盖整棵子树（含之后插入的子节点）。
 *
 * `event.target` 保持浏览器原义（实际被点的元素，可能是 `li` 里的 `span`）；
 * 真实匹配到的宿主元素通过第二个参数给出：`handler(event, {realTarget})`，
 * 同时挂一份在 `event.realTarget` 上，方便只收一个参数的既有处理器。
 * 匹配用 `event.target.closest(selector)`，且要求命中节点仍在容器内 —— 所以事件冒泡
 * 到容器本身、或来自容器外的节点，都不会误触。
 *
 * @param {Element|Document} root 容器（监听器装在这里）
 * @param {string} selector 命中即触发的 CSS 选择器，如 `'li[data-id]'`
 * @param {string} type 事件类型，如 `'click'`
 * @param {(ev: any, ctx: {realTarget: Element}) => void} handler 处理器
 * @param {boolean|{capture?: boolean}} [captureOrOpts] 捕获阶段监听（默认 false）
 * @returns {() => void} 反注册函数
 * @throws {TypeError} root 不是节点、selector/type 非法或 handler 不是函数
 */
export function delegate(root, selector, type, handler, captureOrOpts = false) {
  if (!isDomNode(root)) {
    throw new TypeError(`delegate 的 root 必须是 DOM 节点（容器元素），收到 ${describe(root)}。`);
  }
  if (typeof selector !== 'string' || !selector.trim()) {
    throw new TypeError(
      `delegate 的 selector 必须是非空字符串，收到 ${describe(selector)}（值：${JSON.stringify(selector)}）。`,
    );
  }
  if (typeof type !== 'string' || !type.trim() || /\s/.test(type)) {
    throw new TypeError(
      `delegate 的 type 必须是不含空白的非空事件名，收到 ${JSON.stringify(type)}。`,
    );
  }
  if (typeof handler !== 'function') {
    throw new TypeError(`delegate 的 handler 必须是函数，收到 ${describe(handler)}。`);
  }
  const capture = typeof captureOrOpts === 'object' && captureOrOpts !== null
    ? !!captureOrOpts.capture : !!captureOrOpts;

  const listener = (ev) => {
    const target = /** @type {Element|null} */ (/** @type {any} */ (ev).target);
    if (!target || typeof target.closest !== 'function') return;
    const realTarget = /** @type {Element|null} */ (target.closest(selector));
    if (!realTarget) return;
    if (typeof /** @type {any} */ (root).contains === 'function'
      && !/** @type {any} */ (root).contains(realTarget)) return;
    /** @type {any} */ (ev).realTarget = realTarget; // 不新增参数位，handler 签名仍是 (ev, ctx)
    handler(ev, { realTarget });
  };

  /** @type {any} */ (root).addEventListener(type, listener, capture);
  return () => {
    /** @type {any} */ (root).removeEventListener(type, listener, capture);
  };
}

/**
 * 事件委托（标记模式）：接住 {@link createFragment} 以 `{delegate: true}` 构建的处理器。
 *
 * 该模式下子元素**不调用** `addEventListener`，只把处理器记在 `__rendererEvents` 上并打
 * `data-ev="click ..."` 标记；本函数在容器上为相关类型各装一个监听器，事件到来时从
 * `event.target` 往上找第一个带该类型标记的祖先并调用它的处理器 —— 于是几百行列表只有
 * 几个监听器，而不是几百个闭包。处理器同样收到 `(ev, {realTarget})`，
 * realTarget 是那个带标记的元素。
 *
 * @param {Element} root 容器
 * @param {string[]} [types] 需要监听的类型；缺省为容器内（截至调用时）已标记类型的并集
 * @returns {() => void} 反注册函数
 * @throws {TypeError} root 不是节点，或 types 不是字符串数组
 */
export function delegateEvents(root, types) {
  if (!isDomNode(root)) {
    throw new TypeError(`delegateEvents 的 root 必须是 DOM 节点（容器元素），收到 ${describe(root)}。`);
  }
  /** @type {Set<string>} */
  const wanted = new Set();
  if (types === undefined) {
    for (const mark of collectMarks(root)) wanted.add(mark);
    if (!wanted.size) wanted.add('click'); // 没有标记时也要让调用方拿到一个可用的挂载点
  } else if (Array.isArray(types)) {
    for (const t of types) {
      if (typeof t !== 'string' || !t) {
        throw new TypeError(`delegateEvents 的 types 必须是非空字符串数组，收到元素 ${describe(t)}。`);
      }
      wanted.add(t);
    }
  } else {
    throw new TypeError(`delegateEvents 的 types 必须是字符串数组，收到 ${describe(types)}。`);
  }

  /** @type {Array<() => void>} */
  const offs = [];
  for (const type of wanted) {
    // 不用 closest(selector)：标记要沿祖先链找，点到标记按钮里的 span 也得命中。
    const listener = (ev) => {
      /** @type {any} */
      let node = /** @type {any} */ (ev).target;
      while (node && node !== /** @type {any} */ (root).parentNode) {
        if (node.nodeType === 1 && markedTypes(node).has(type)) {
          const list = /** @type {any} */ (node).__rendererEvents;
          /** @type {any} */ (ev).realTarget = node; // 不新增参数位，handler 签名不变
          if (Array.isArray(list)) for (const [t, fn] of list) if (t === type) fn(ev, { realTarget: node });
          return;
        }
        node = node.parentNode;
      }
    };
    /** @type {any} */ (root).addEventListener(type, listener);
    offs.push(() => /** @type {any} */ (root).removeEventListener(type, listener));
  }
  return () => {
    for (const off of offs) off();
  };
}

/** 内部工具：找出元素上"标记模式"登记的类型（data-ev 或 __rendererEvents）。 */
function markedTypes(el) {
  /** @type {Set<string>} */
  const out = new Set();
  const list = /** @type {any} */ (el).__rendererEvents;
  if (Array.isArray(list)) for (const [t] of list) out.add(t);
  const mark = typeof el.getAttribute === 'function' ? el.getAttribute('data-ev') : null;
  if (mark) for (const t of mark.split(/\s+/)) if (t) out.add(t);
  return out;
}

/** 内部工具：深度收集子树里出现过的标记类型（描述对象/真实节点都能走）。 */
function collectMarks(root) {
  /** @type {Set<string>} */
  const acc = new Set();
  const walk = (node) => {
    if (!node || node.nodeType !== 1) return;
    for (const t of markedTypes(/** @type {any} */ (node))) acc.add(t);
    const kids = node.children || node.childNodes || [];
    for (let i = 0; i < kids.length; i += 1) walk(kids[i]);
  };
  for (const t of markedTypes(/** @type {any} */ (root))) acc.add(t);
  walk(root);
  return acc;
}

/**
 * 转义 HTML 文本与属性值里的特殊字符。
 * @param {unknown} text
 * @returns {string}
 */
export function escapeHTML(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * 把 {@link createFragment} 构建出的节点序列化为 HTML 字符串。
 *
 * 用途：服务端渲染、快照断言、调试打印。文本与属性值一律经 {@link escapeHTML}；
 * 注释节点不输出（HTML 注释带条件注释等历史行为）；事件绑定不是属性，本来就不会出现。
 *
 * @param {Element|Text|Comment} element 待序列化的节点
 * @returns {string} HTML 片段
 * @throws {TypeError} element 为 null/undefined 或不是 DOM 节点
 */
export function renderToHTML(element) {
  return serialize(normalizeElementNode(element));
}

/**
 * 内部递归序列化。
 * @param {any} node
 * @returns {string}
 */
function serialize(node) {
  if (node.nodeType === 3) return escapeHTML(node.data ?? node.nodeValue ?? '');
  if (node.nodeType === 8) return '';
  if (node.nodeType === 11) {
    let frag = '';
    for (let i = 0; i < node.childNodes.length; i += 1) frag += serialize(node.childNodes[i]);
    return frag;
  }
  const tag = String(node.tagName || node.nodeName).toLowerCase();
  /** @type {string[]} */
  const rendered = [];
  const seen = new Set();
  const attrs = node.attributes;
  const len = attrs && typeof attrs.length === 'number' ? attrs.length : 0;
  for (let i = 0; i < len; i += 1) {
    const attr = attrs[i];
    if (!attr) continue;
    const name = String(attr.name).toLowerCase();
    if (seen.has(name)) continue; // 同名只出一次，避免产出 `class="a" class="b"`
    seen.add(name);
    const value = String(attr.value);
    if (BOOLEAN_ATTRIBUTES.has(name)) {
      if (value !== '' && value !== 'false') rendered.push(name);
      continue;
    }
    rendered.push(`${name}="${escapeHTML(value)}"`);
  }
  const head = `<${tag}${rendered.length ? ` ${rendered.join(' ')}` : ''}>`;
  if (VOID_TAGS.has(tag)) return head;
  let body = '';
  for (let child = node.firstChild; child; child = child.nextSibling) body += serialize(child);
  return `${head}${body}</${tag}>`;
}

// ---------------------------------------------------------------------------
// 自测：node src/renderer.js --selftest
// 沿用 scripts/fit_kappa.py 的约定 —— 每条判据都要一对"必须开火 / 必须不开火"的控制，
// 看不见开火的探针不算证据。桩件只为让断言能在 Node 里跑，不随浏览器路径加载。
// ---------------------------------------------------------------------------

/**
 * 极简 DOM 桩件：提供 document、Node.prototype 的 brand-checked nodeType 访问器，
 * 以及够用的 closest / contains / 派发。
 * @returns {Document}
 */
function makeDomStub() {
  /** @type {WeakSet<object>} */
  const realNodes = new WeakSet();

  class StubNode {
    /** @param {number} type @param {string} name */
    constructor(type, name) {
      this._type = type;
      this.nodeName = name;
      this.childNodes = [];
      this.parentNode = null;
      this.attributes = [];
      /** @type {Map<string, Function[]>} */
      this._listeners = new Map();
      realNodes.add(this);
    }

    /** 模仿 WebIDL brand check：非 Node 的 this 一律抛，正是 isDomNode 要利用的行为。 */
    get nodeType() {
      if (!realNodes.has(this)) {
        throw new TypeError("Failed to read 'nodeType' on 'Node': parameter 1 is not of type 'Node'.");
      }
      return this._type;
    }

    get tagName() {
      if (this.nodeType !== 1) return undefined;
      // 与真实 DOM 一致：HTML 元素的 tagName 大写，SVG 保留原写法（foreignObject 等）。
      return this.namespaceURI && this.namespaceURI !== HTML_NS
        ? this._tag : String(this._tag).toUpperCase();
    }

    get children() { return this.childNodes.filter((c) => c.nodeType === 1); }

    get firstChild() { return this.childNodes[0] || null; }

    get nextSibling() {
      if (!this.parentNode) return null;
      const sibs = this.parentNode.childNodes;
      const i = sibs.indexOf(this);
      return i >= 0 && i + 1 < sibs.length ? sibs[i + 1] : null;
    }

    appendChild(child) {
      if (child.parentNode) child.parentNode.removeChild(child);
      child.parentNode = this;
      this.childNodes.push(child);
      return child;
    }

    removeChild(child) {
      const i = this.childNodes.indexOf(child);
      if (i >= 0) this.childNodes.splice(i, 1);
      child.parentNode = null;
      return child;
    }

    contains(node) {
      /** @type {any} */
      let cur = node;
      while (cur) {
        if (cur === this) return true;
        cur = cur.parentNode;
      }
      return false;
    }

    setAttribute(name, value) {
      const hit = this.attributes.find((a) => a.name === name);
      if (hit) hit.value = String(value);
      else this.attributes.push({ name, value: String(value) });
    }

    getAttribute(name) {
      const hit = this.attributes.find((a) => a.name === name);
      return hit ? hit.value : null;
    }

    removeAttribute(name) {
      const i = this.attributes.findIndex((a) => a.name === name);
      if (i >= 0) this.attributes.splice(i, 1);
    }

    addEventListener(type, fn) {
      if (!this._listeners.has(type)) this._listeners.set(type, []);
      this._listeners.get(type).push(fn);
    }

    removeEventListener(type, fn) {
      const list = this._listeners.get(type);
      if (!list) return;
      const i = list.indexOf(fn);
      if (i >= 0) list.splice(i, 1);
    }

    /** 桩件用的冒泡派发：target 固定为 this，逐个祖先调用监听器（只传 event 一个参数）。 */
    fire(ev) {
      ev.target = this;
      /** @type {any} */
      let node = this;
      while (node) {
        const list = node._listeners.get(ev.type) || [];
        for (const fn of list.slice()) fn.call(node, ev);
        node = node.parentNode;
      }
      return ev;
    }

    closest(selector) {
      /** @type {any} */
      let node = this;
      while (node) {
        if (node.nodeType === 1 && matchesStub(node, selector)) return node;
        node = node.parentNode;
      }
      return null;
    }
  }

  Object.defineProperty(StubNode.prototype, 'nodeType', {
    ...Object.getOwnPropertyDescriptor(StubNode.prototype, 'nodeType'),
    enumerable: true,
  });

  class StubElement extends StubNode {
    /** @param {string} name @param {string|null} [ns] */
    constructor(name, ns = null) {
      super(1, name.toUpperCase());
      this._tag = name;
      this.namespaceURI = ns;
    }
  }

  class StubText extends StubNode {
    /** @param {string} data */
    constructor(data) {
      super(3, '#text');
      this.data = data;
    }
  }

  class StubDocument extends StubNode {
    constructor() {
      super(9, '#document');
      this.createElement = (name) => new StubElement(name);
      this.createElementNS = (ns, name) => new StubElement(name, ns);
      this.createTextNode = (data) => new StubText(data);
      this.createComment = (data) => new StubNode(8, '#comment');
      this.createDocumentFragment = () => new StubNode(11, '#document-fragment');
    }
  }

  /** 只支持自测用到的简单选择器：`tag`、`.cls`、`#id`、`[attr]`、`[attr="v"]` 及其组合。 */
  function matchesStub(el, selector) {
    if (selector === '*') return true;
    const parts = selector.match(/^([a-zA-Z][\w-]*)?((?:[#.][\w-]+|\[[^\]]+\])*)$/);
    if (!parts) return false;
    if (parts[1] && String(el.tagName).toLowerCase() !== parts[1].toLowerCase()) return false;
    for (const token of (parts[2] || '').match(/[#.][\w-]+|\[[^\]]+\]/g) || []) {
      if (token[0] === '#') {
        if (el.getAttribute('id') !== token.slice(1)) return false;
      } else if (token[0] === '.') {
        if (!(el.getAttribute('class') || '').split(/\s+/).includes(token.slice(1))) return false;
      } else {
        const body = token.slice(1, -1);
        const eq = body.indexOf('=');
        if (eq < 0) {
          if (el.getAttribute(body) === null) return false;
        } else {
          const want = body.slice(eq + 1).replace(/^["']|["']$/g, '');
          if (el.getAttribute(body.slice(0, eq)) !== want) return false;
        }
      }
    }
    return true;
  }

  const document = new StubDocument();
  globalThis.document = /** @type {any} */ (document);
  globalThis.Node = /** @type {any} */ (StubNode);
  return /** @type {any} */ (document);
}

/**
 * 自测结果收集处。放在模块级是为了让「某条断言把整轮自测带走」这种中断也能报出
 * 已完成到哪一条控制 —— 中断若只留一个 exit 1 和一段栈，读数就不可归因。
 * @type {Array<{name: string, passed: boolean, detail?: string}>}
 */
const SELFTEST_CHECKS = [];

/**
 * 自测控制集。
 * @returns {number} 退出码（0 = 全过）
 */
function runSelftest() {
  const doc = makeDomStub();
  SELFTEST_CHECKS.length = 0;

  /** @type {Array<{name: string, passed: boolean, detail?: string}>} */
  const checks = SELFTEST_CHECKS;
  const ok = (name, passed, detail = '') => checks.push({ name, passed: !!passed, detail });
  /** @param {() => void} fn @returns {Error|null} */
  const grab = (fn) => {
    try {
      fn();
      return null;
    } catch (e) {
      return e instanceof TypeError ? e : new Error(`非 TypeError: ${String(e)}`);
    }
  };

  // ---- C1 字符串子节点：必须成为文本节点，绝不进 innerHTML ----
  const payload = '<img src=x onerror=alert(1)>';
  const c1 = createFragment('div', { class: 'card' }, [payload]);
  ok('C1 字符串子节点走 createTextNode，宿主里不被解析成元素',
    c1.childNodes.length === 1 && c1.childNodes[0].nodeType === 3
    && c1.childNodes[0].data === payload);
  ok('C1b 元素身上没有多出任何 HTML 属性（innerHTML 无痕迹）',
    c1.attributes.length === 1 && c1.attributes[0].name === 'class');

  // ---- C2 归一化：真实节点原样挂、伪节点拒 ----
  const pre = doc.createElement('span');
  const c2 = createFragment('div', {}, [pre]);
  ok('C2 已是真实节点的子对象原样挂载（未重复创建）',
    c2.childNodes.length === 1 && c2.childNodes[0] === pre);
  ok('C2b normalizeElementNode 接受宿主造出的 text 节点',
    normalizeElementNode(doc.createTextNode('hi')).nodeType === 3);
  ok('C2c isDomNode 拒伪造的节点样对象（宿主 brand check 骗不过）',
    isDomNode({ nodeType: 1, tagName: 'IMG' }) === false);
  ok('C2d normalizeElementNode 对伪造对象抛 TypeError 并说明形状',
    /nodeType/.test(String(grab(() => normalizeElementNode({ nodeType: 1, tagName: 'IMG' })))));
  ok('C2e normalizeElementNode 拒字符串', !!grab(() => normalizeElementNode('<b>粗</b>')));
  ok('C2f normalizeElementNode 展开描述对象（顶层简写属性 + 嵌套 attrs）',
    renderToHTML(normalizeElementNode({ tagName: 'img', src: '/a.png' })) === '<img src="/a.png">'
    && renderToHTML(normalizeElementNode({
      tagName: 'div', attrs: { class: 'x' }, children: [{ tagName: 'b', children: ['hi'] }],
    })) === '<div class="x"><b>hi</b></div>');
  ok('C2g normalizeElementNode(null) 抛错', !!grab(() => normalizeElementNode(null)));
  ok('C2h isDomNode 的字段探测退化分支：无 Node 构造环境仍能认节点（本桩件走 brand check，故以对象对照）',
    (() => {
      const saved = globalThis.Node;
      delete /** @type {any} */ (globalThis).Node;
      const viaField = isDomNode(doc.createElement('i')); // 退化分支：读 nodeType 字段
      const stillRejectsPlain = isDomNode({ nodeType: 1 });
      globalThis.Node = saved;
      return viaField === true && stillRejectsPlain === true;
    })());

  // ---- C3 属性 key 小写归一 + class/for ----
  const c3 = createFragment('div', { CLASS: 'a', class: 'b', For: 'x', tabIndex: '2' }, []);
  ok('C3 属性 key 统一小写写入，同名合并（CLASS 与 class 都落到 class）',
    c3.getAttribute('class') === 'a b' && c3.getAttribute('for') === 'x'
    && c3.getAttribute('tabindex') === '2',
    `class=${JSON.stringify(c3.getAttribute('class'))}`);
  ok('C3b class 值可按空格拆出两项（classList 语义成立）',
    (c3.getAttribute('class') || '').split(/\s+/).filter(Boolean).length === 2);

  // ---- C4 值类型与事件属性 ----
  ok('C4 字符串内联事件 onclick 抛 TypeError 且指名属性',
    /onclick/.test(String(grab(() => createFragment('div', { onclick: 'alert(1)' })))));
  ok('C4b 大小写变体 OnClick / onMouseOver 同样拒（防绕过）',
    !!grab(() => createFragment('div', { OnClick: 'alert(1)' }))
    && !!grab(() => createFragment('div', { onMouseOver: 'x' })));
  ok('C4c 非字符串属性值（number / boolean / 数组）抛 TypeError',
    !!grab(() => createFragment('div', { title: 42 }))
    && !!grab(() => createFragment('div', { hidden: true }))
    && !!grab(() => createFragment('div', { rel: ['a'] })));
  ok('C4d data-*/aria-* 放宽：任意字符串放行（哑火控制）',
    !grab(() => createFragment('div', {
      'data-raw': 'a"b<c>', 'aria-label': 'ok', 'data-onclick': 'not-a-handler',
    })));
  ok('C4e 函数事件属性放行且注册为监听器而非属性',
    (() => {
      const el = createFragment('button', { onclick: () => {} }, ['go']);
      return el.attributes.every((a) => !/^on/i.test(a.name))
        && (el._listeners.get('click') || []).length === 1;
    })());
  ok('C4f on:click / on-click 归一为 click，处理器收到 (ev, ctx) 且 ctx.realTarget 是本元素',
    (() => {
      /** @type {any[]} */
      const seen = [];
      const el = createFragment('button', { 'on:click': (ev, ctx) => seen.push([ev, ctx]) }, []);
      el.fire({ type: 'click' });
      return seen.length === 1 && seen[0][0].type === 'click' && seen[0][1].realTarget === el;
    })());
  ok('C4g onMouseOver 驼峰写法归一为 mouseover（函数值时）',
    (() => {
      const el = createFragment('div', { onMouseOver: () => {} }, []);
      return (el._listeners.get('mouseover') || []).length === 1;
    })());
  ok('C4h data-onclick 不被当成事件绑定（前缀豁免优先）',
    (() => {
      const el = createFragment('button', { 'data-onclick': 'noop' }, []);
      return el.getAttribute('data-onclick') === 'noop'
        && el._listeners.size === 0;
    })());
  ok('C4i null/undefined 属性值 = 不写该属性，且不抛',
    (() => {
      const el = createFragment('div', { title: null, lang: undefined }, []);
      return el.attributes.length === 0;
    })());
  ok('C4j style / srcdoc 一律拒',
    !!grab(() => createFragment('div', { style: 'x' }))
    && !!grab(() => createFragment('iframe', { srcdoc: '<b>' })));
  ok('C4k on 前缀的普通属性名（once / onestep）按规格判为事件类：字符串值拒、函数值放行',
    !!grab(() => createFragment('div', { once: 'a' }))
    && !!grab(() => createFragment('div', { onestep: 'x' }))
    && !grab(() => createFragment('div', { once: () => {} })));
  ok('C4l opacity 以 op 开头而非 on，不受事件规则影响（哑火控制）',
    !grab(() => createFragment('div', { opacity: '0.5' })));
  ok('C4m 属性名含非法字符（空格/引号/尖括号）拒',
    !!grab(() => createFragment('div', { 'a b': 'c' }))
    && !!grab(() => createFragment('div', { 'a<b': 'c' }))
    && !!grab(() => createFragment('div', { '': 'c' })));

  // ---- C5 危险协议 ----
  const badUrls = [
    'javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'java\tscript:alert(1)',
    ' javascript:alert(1)', 'data:text/html;base64,PHNjcmlwdD4=',
    'vbscript:msgbox(1)', 'livescript:alert(1)', 'mocha:alert(1)',
  ];
  const rejected = badUrls.filter((u) => grab(() => createFragment('a', { href: u }, [])));
  ok('C5 href 的危险协议全部被拒（含大小写/制表符/前导空白变形）',
    rejected.length === badUrls.length, `${rejected.length}/${badUrls.length}`);
  ok('C5b 每个 URL 类属性走同一判定（src/action/formaction/poster/ping/xlink:href/object data）',
    ['src', 'action', 'formaction', 'poster', 'ping', 'xlink:href', 'data'].every(
      (k) => !!grab(() => createFragment('a', { [k]: 'javascript:alert(1)' }, [])),
    ));
  ok('C5c 安全 URL 放行（哑火控制：防判据宽到误伤合法链接）',
    !grab(() => createFragment('a', {
      href: 'https://example.com/x?a=1&b=2#frag', src: '/local/path.png', ping: 'about:blank',
    }, [])));
  ok('C5d data-* 值里出现 javascript: 不误伤（它不是 URL 属性）',
    !grab(() => createFragment('div', { 'data-href': 'javascript:alert(1)' })));

  // ---- C6 命名空间 ----
  const svgRoot = createFragment('svg', { width: '10' }, [
    createFragment('circle', { cx: '5' }, []),
    'svg 里的文本',
  ]);
  ok('C6 svg 根走 createElementNS(SVG_NS)', svgRoot.namespaceURI === SVG_NS);
  ok('C6b SVG 专有子元素自动带 SVG_NS（circle 命中共享名之外的专有名）',
    svgRoot.childNodes[0]?.namespaceURI === SVG_NS);
  ok('C6c 文本子节点照常挂进 SVG 树', svgRoot.childNodes[1]?.nodeType === 3);
  ok('C6d SVG 元素上的 class 仍写入（className 在 SVG 侧是只读对象）',
    (() => {
      const g = createFragment('g', { class: 'layer' }, []);
      return g.getAttribute('class') === 'layer' && g.namespaceURI === SVG_NS;
    })());
  ok('C6e math 根走 MATHML_NS 且子元素继承',
    (() => {
      const m = createFragment('math', {}, [{ tagName: 'mi', children: ['x'] }]);
      return m.namespaceURI === MATHML_NS && m.childNodes[0]?.namespaceURI === MATHML_NS;
    })());
  ok('C6f 共享名 a 默认 HTML，显式 opts.namespace 才落到 SVG',
    createFragment('a', {}, []).namespaceURI !== SVG_NS
    && createFragment('a', {}, [], { namespace: SVG_NS }).namespaceURI === SVG_NS);
  ok('C6g 命名空间沿描述对象子树继承（svg 内的 a 走 SVG）',
    (() => {
      const s = createFragment('svg', {}, [{ tagName: 'a' }]);
      return s.childNodes[0]?.namespaceURI === SVG_NS;
    })());
  ok('C6h 普通 HTML 元素不带命名空间（哑火控制）',
    createFragment('div', {}, []).namespaceURI !== SVG_NS);

  // ---- C7 children 非法值与归一 ----
  ok('C7 非节点且非描述对象的子节点抛 TypeError',
    !!grab(() => createFragment('div', {}, [{ href: 'x' }])));
  ok('C7b 数字转文本、嵌套数组摊平、空槽位跳过',
    (() => {
      const el = createFragment('div', {}, [1, ['a', ['b']], null, false, undefined, 0]);
      return el.childNodes.length === 4 && el.childNodes[0].data === '1'
        && el.childNodes[2].data === 'b' && el.childNodes[3].data === '0';
    })());
  ok('C7c 非法 NaN 子节点抛错', !!grab(() => createFragment('div', {}, [NaN])));
  ok('C7d tagName 必须是合法标签名（对象/空串/undefined/带空格一律拒）',
    !!grab(() => createFragment({ tagName: 'div' }, {}, []))
    && !!grab(() => createFragment('', {}, []))
    && !!grab(() => createFragment(undefined, {}, []))
    && !!grab(() => createFragment('<div>', {}, [])));
  ok('C7e attrs 传成数组（把 children 挪到第 2 位的笔误）时报错指名第 3 个参数',
    /第 3 个参数/.test(String(grab(() => createFragment('div', ['a'], [])))));
  ok('C7f opts.namespace 传非字符串（数字/空串）抛错',
    !!grab(() => createFragment('div', {}, [], { namespace: 123 }))
    && !!grab(() => createFragment('div', {}, [], { namespace: '' })));

  // ---- C8 renderList ----
  ok('C8 renderList 正常映射并带上 index',
    renderList(['a', 'b'], (it, i) => createFragment('li', {}, [`${i}:${it}`])).length === 2
    && renderList(['x'], (it) => it)[0] === 'x');
  ok('C8b renderList 非数组抛错且写明实际类型',
    /必须是数组/.test(String(grab(() => renderList({}, (x) => x)))));
  ok('C8c 字符串有 length 但不算列表，照样抛错',
    !!grab(() => renderList('abc', (x) => x)));
  ok('C8d 空数组与 null/undefined 都走 fallback',
    renderList([], (x) => x, 'F')[0] === 'F' && renderList(null, (x) => x, 'F')[0] === 'F'
    && renderList(undefined, (x) => x, 'F')[0] === 'F');
  ok('C8e fallback 默认 null 时返回空数组（可安全摊进 children）',
    Array.isArray(renderList([], (x) => x)) && renderList([], (x) => x).length === 0
    && !grab(() => createFragment('ul', {}, renderList(null, (x) => x))));
  ok('C8f itemRenderer 非函数抛 TypeError（列表为空也先校验）',
    !!grab(() => renderList([], 'nope')) && !!grab(() => renderList(['a'], {})));
  ok('C8g renderList 的结果可直接作为 children（端到端）',
    renderToHTML(createFragment('ul', { class: 'l' },
      renderList(['a'], (it) => createFragment('li', {}, [it])))) === '<ul class="l"><li>a</li></ul>');

  // ---- C9 事件委托（选择器模式）----
  const rows = createFragment('ul', {}, renderList(
    ['a', 'b'],
    (it) => createFragment('li', { class: 'row', 'data-id': it }, [
      createFragment('span', {}, [it]),
    ]),
  ));
  /** @type {any[]} */
  const hits = [];
  const off = delegate(rows, 'li[data-id]', 'click', (ev, ctx) => hits.push({
    target: ev.target.tagName, real: ctx.realTarget, evReal: ev.realTarget,
  }));
  const span = rows.childNodes[0]?.childNodes[0];
  span?.fire({ type: 'click' }); // 点的是孙子节点
  ok('C9 event.target 保持实际被点的元素（不被改写）',
    hits.length === 1 && hits[0].target === 'SPAN');
  ok('C9b handler 收到的 ctx.realTarget 是真实匹配的 li',
    hits.length === 1 && hits[0].real.getAttribute('data-id') === 'a');
  ok('C9c event.realTarget 同步挂上，只收一个参数的既有处理器也能用',
    hits.length === 1 && hits[0].evReal === hits[0].real);
  ok('C9d 一个监听器覆盖全子树（第二行也命中，且监听器仍只有一个）',
    (() => {
      const span2 = rows.childNodes[1]?.childNodes[0];
      span2.fire({ type: 'click' });
      return hits.length === 2 && hits[1].real.getAttribute('data-id') === 'b'
        && (rows._listeners.get('click') || []).length === 1;
    })());
  ok('C9e 点容器自身（不匹配选择器）不触发（哑火控制）',
    (() => {
      const before = hits.length;
      rows.fire({ type: 'click' });
      return hits.length === before;
    })());
  off();
  ok('C9f 反注册之后不再触发',
    (() => {
      const before = hits.length;
      span?.fire({ type: 'click' });
      return hits.length === before && (rows._listeners.get('click') || []).length === 0;
    })());
  ok('C9g delegate 入参校验：root/selector/type/handler 各自抛 TypeError',
    !!grab(() => delegate({}, 'li', 'click', () => {}))
    && !!grab(() => delegate(rows, '', 'click', () => {}))
    && !!grab(() => delegate(rows, 'li', 'click x', () => {}))
    && !!grab(() => delegate(rows, 'li', 'click', 'nope')));

  // ---- C9h 事件委托（标记模式 opts.delegate + delegateEvents）----
  // 标记模式要在**构建那个元素时**传 opts.delegate（opts 只对当次调用生效，
  // 描述对象子节点才沿树继承），所以列表项通常在 itemRenderer 里逐个传。
  const marked = createFragment('div', {}, [
    createFragment('button', {
      id: 'b1',
      onclick: (ev, ctx) => { ev.__hit = ctx.realTarget.getAttribute('id'); },
    }, [createFragment('span', {}, ['x'])], { delegate: true }),
    createFragment('em', {}, ['无处理器']),
  ]);
  const offMark = delegateEvents(marked);
  const btn = marked.childNodes[0];
  const btnSpan = btn?.childNodes[0];
  ok('C9h 标记模式下子元素不注册监听器，只打 data-ev 标记',
    (btn?._listeners.get('click') || []).length === 0 && btn?.getAttribute('data-ev') === 'click');
  ok('C9i delegateEvents 在容器上只装一个监听器',
    (marked._listeners.get('click') || []).length === 1);
  const evM = btnSpan?.fire({ type: 'click' }); // 点的是按钮里的 span，仍应归到按钮
  ok('C9j 标记模式沿祖先链找标记，realTarget 是带标记的按钮本身', evM.__hit === 'b1');
  ok('C9k 点无标记的兄弟节点不误触发（哑火控制）',
    (() => {
      const probe = { type: 'click' };
      marked.childNodes[1]?.fire(probe);
      return probe.__hit === undefined;
    })());
  offMark();
  ok('C9l delegateEvents 反注册后不再派发',
    (() => {
      const probe = { type: 'click' };
      btn?.fire(probe);
      return probe.__hit === undefined;
    })());

  // ---- C10 renderToHTML ----
  const tree = createFragment('div', { class: 't' }, [
    createFragment('p', {}, ['<b>&"粗"']),
    createFragment('input', { type: 'text', value: 'a"b' }),
    createFragment('br', {}, []),
    '尾部文本',
  ]);
  const html = renderToHTML(tree);
  ok('C10 文本与属性值都转义',
    html === '<div class="t"><p>&lt;b&gt;&amp;&quot;粗&quot;</p>'
      + '<input type="text" value="a&quot;b"><br>尾部文本</div>',
    html);
  ok('C10b 空元素不出闭合标签，普通元素必出且不自我闭合',
    html.includes('<br>') && !html.includes('<br/>') && html.includes('</p>'));
  ok('C10c 布尔属性只出名字不出 ="true"',
    renderToHTML(createFragment('input', { type: 'checkbox', checked: 'true' }, []))
      === '<input type="checkbox" checked>');
  ok('C10d renderToHTML 拒字符串与 null',
    !!grab(() => renderToHTML('<div>x</div>')) && !!grab(() => renderToHTML(null)));
  ok('C10e escapeHTML 覆盖 & < > " \' 五个字符',
    escapeHTML('&<>"\'') === '&amp;&lt;&gt;&quot;&#39;');
  ok('C10f 注释节点不输出（防条件注释等历史行为）',
    renderToHTML(createFragment('div', {}, [doc.createComment('hi')])) === '<div></div>');
  ok('C10g 事件函数不会出现在序列化结果里（它从来不是属性）',
    !renderToHTML(createFragment('button', { onclick: () => {} }, ['go'])).includes('onclick'));

  // ---- C11 sanitizeAttributes 与 validateAttributes 同判（别名，不是第二套规则）----
  const dirty = {
    class: 'ok', 'data-x': '<script>', onclick: 'alert(1)', style: 'x: y',
    href: 'javascript:alert(1)', HREF: 'javascript:alert(2)', src: '/ok.png',
    srcdoc: '<b>', 'aria-label': 'ok', title: 42, 'on-click': () => {},
  };
  const clean = sanitizeAttributes(dirty);
  ok('C11 sanitizeAttributes 摘掉 style / srcdoc / 字符串 onclick',
    !('style' in clean) && !('onclick' in clean) && !('srcdoc' in clean),
    JSON.stringify(Object.keys(clean)));
  ok('C11b 大写 HREF 归一后同样被拦（防大小写绕过）', !('href' in clean));
  ok('C11c 保留合规项并归一小写键', clean.class === 'ok' && clean.src === '/ok.png'
    && clean['data-x'] === '<script>' && clean['aria-label'] === 'ok');
  ok('C11d 函数形态的 on-click 也摘掉（没有包装可用时不产生副作用）',
    !('onclick' in clean) && !('on-click' in clean));
  ok('C11e 非对象入参抛 TypeError', !!grab(() => sanitizeAttributes('abc'))
    && !!grab(() => sanitizeAttributes([])));
  ok('C11f 两把尺子同判：非事件键在 validate 通过 ⟺ 在 sanitize 保留',
    Object.keys(dirty)
      .filter((k) => {
        const n = normalizeAttrKey(k);
        return !(n.startsWith('on') && !n.startsWith('data-') && !n.startsWith('aria-'));
      })
      .every((k) => (!grab(() => validateAttributes({ [k]: dirty[k] })))
        === (normalizeAttrKey(k) in clean)),
    `clean=${JSON.stringify(Object.keys(clean))}`);
  ok('C11g sanitizeAttributes 的产物一定能过 validateAttributes（端到端闭环）',
    !grab(() => validateAttributes(clean)));

  // ---- C12 环境缺失 ----
  const savedDoc = globalThis.document;
  delete /** @type {any} */ (globalThis).document;
  const envErr = grab(() => createFragment('div'));
  globalThis.document = savedDoc;
  ok('C12 无全局 document 时给出可定位的 TypeError（不是读 undefined 属性）',
    envErr instanceof TypeError && /document/.test(envErr.message));

  // ---- 报告 ----
  let failed = 0;
  console.log('== renderer.js selftest ==');
  for (const { name, passed, detail } of checks) {
    if (!passed) failed += 1;
    console.log(`  ${passed ? 'PASS' : 'FAIL'}  ${name}${detail ? `   <- ${detail}` : ''}`);
  }
  console.log(`controls : ${checks.length}`);
  console.log(`failed   : ${failed}`);
  console.log(failed ? 'RESULT: FAILURES PRESENT' : 'RESULT: ALL PASS');
  return failed ? 1 : 0;
}

/**
 * 是否在 Node 里以 CLI 方式运行本文件（浏览器里 import.meta.filename 不存在）。
 * @returns {boolean}
 */
function invokedAsCli() {
  const meta = /** @type {any} */ (typeof import.meta === 'undefined' ? {} : import.meta);
  return typeof process !== 'undefined'
    && Array.isArray(process.argv)
    && typeof meta.filename === 'string'
    && process.argv[1] === meta.filename;
}

if (invokedAsCli() && process.argv.slice(2).includes('--selftest')) {
  let code = 1;
  try {
    code = runSelftest();
  } catch (err) {
    // 一条断言抛异常会带走后面的控制：这里把它归因成一行 FAIL 并照样打出标记行，
    // 否则调用方只看到一个 exit 1 和一段栈，分不清"判据红"与"尺子坏"。
    const done = SELFTEST_CHECKS.length;
    const failedSoFar = SELFTEST_CHECKS.filter((c) => !c.passed).length;
    console.log('== renderer.js selftest ==');
    for (const { name, passed, detail } of SELFTEST_CHECKS) {
      console.log(`  ${passed ? 'PASS' : 'FAIL'}  ${name}${detail ? `   <- ${detail}` : ''}`);
    }
    console.log(`  FAIL  自测中断：第 ${done + 1} 条控制的断言抛了异常`
      + `（${done ? `上一条为「${SELFTEST_CHECKS[done - 1].name}」` : '尚无已完成控制'}）`
      + `   <- ${String(err && err.message ? err.message : err)}`);
    console.log('controls : UNBOUNDED（已中断，分母不可信）');
    console.log(`failed   : ${failedSoFar + 1}`);
    console.log('RESULT: ABORTED');
    code = 2; // 与"判据红"(1) 分开：2 = 尺子自己坏了
  }
  process.exitCode = code;
}
