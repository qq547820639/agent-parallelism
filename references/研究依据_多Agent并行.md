# 多 Agent 激进并行策略 · 深度研究报告

> 研究对象：初稿《多 Agent 并行策略评估》（本工作区 2026-09-09）
> 研究方法：对初稿每一条核心论断做外部证据对照，标注【证实】【修正】【推翻】
> 报告日期：2026-09-09

---

## 0. 执行摘要

**初稿主结论成立，但两处关键技术论断需要修正。**

| 初稿论断 | 判定 | 依据 |
|---|---|---|
| 激进并行不作为默认策略 | **证实** | Nature MI 2026 受控实验：260 配置中多 Agent 收益均值 0.0%，编码类全部转负 |
| 并发度应随上下文耦合度变化（分层并行） | **证实** | Nature MI：金融分析 +80.8%，强顺序规划 −70.0%，差异由"可分解性"而非复杂度决定 |
| K≈4~8 存在峰值，之后掉头下降 | **证实（但机制说错了）** | 峰值真实存在，但驱动力是**超线性协调开销**（实测指数 1.724），不是初稿假设的线性合并成本 |
| K=2~4 用于写入、K=8~16 用于只读 | **证实** | Anthropic 生产系统实际用 3~5 个子 agent；Nature MI 中"独立"结构错误放大 17.2× |
| 「并行是放大上下文约束而非缓解」 | **部分修正** | 上下文复制确实是成本（15× token），但**隔离本身也是收益**——子 agent 独立窗口避免上下文污染，这是多 Agent 存在的核心理由之一 |
| 「重复加载 K 份背景上下文 = 纯浪费」 | **推翻（措辞错误）** | 同等 token 预算下多 Agent 输给单 Agent（Stanford arXiv:2604.02460），说明多花的 token 确实买到了东西；问题是**贵**，不是**浪费** |
| 契约先行是最高杠杆 | **证实且被低估** | MAST 数据显示"规格类"失败占 44.2%，为第一大失效类别；仅修角色规格即 +9.4% 成功率 |
| 验收门禁 | **应升级为 P0** | 无中心验证时错误放大 17.2×，有中心协调器降至 4.4×；MAST 中加高层验证步骤 +15.6% |

**新增（初稿完全缺失的两条硬规则）**：
1. **45% 能力饱和阈值**：先测单 agent 基线，基线 >45% 时加 agent 通常归零或转负。该规则在 SWE-bench Verified / Terminal-Bench 的 16 个配置上方向命中 94%。
2. **冗余率甜点 ≈0.41**：注入子 agent 的共享背景占比 >0.50 后与成功率负相关。这给"briefing 该写多长"提供了可量化目标。

---

## 1. 定量模型升级：从线性假设到通用可扩展性定律（USL）

### 1.1 初稿模型的问题

初稿假设「合并成本 = 0.25×(K−1)」——**线性**。这是低估。

真正描述并行系统吞吐的是 Gunther 通用可扩展性定律（USL，1993）：

```
C(N) = N / (1 + σ(N−1) + κN(N−1))
```

- `σ`：contention，串行/排队部分（↔ 初稿的 L0 契约层 + L3 收敛层）
- `κ`：coherency，一致性/串扰开销，**随 N(N−1) 二次增长**（↔ 上下文碎片化、契约漂移、合并对账）
- 当 `κ = 0` 时 USL 退化为 Amdahl 定律 —— 即 Amdahl 只是 κ=0 的理想上界

**关键推论：存在最优并发度，超过后吞吐不增反降（retrograde scalability）：**

```
N_max = √((1 − σ) / κ)
```

含义极其直接：**并发度上界由「串行占比」与「串扰系数」的比值开方决定，而不是拍脑袋。**

### 1.2 实测校准：Nature MI 2026

在固定总推理预算下实测：**协调回合数随 agent 数超线性增长，拟合指数 1.724，R² = 0.974**。

这个数字落在 1.0（初稿的线性假设）与 2.0（USL 的二次项）之间，且明显靠近 2.0。
→ **结论：初稿低估了协调成本；用 USL 二次项做规划是保守但正确的。**

### 1.3 两档参数下的 USL 曲线（可直接用于定 K）

| 并发 K | 高耦合（写入/共享状态）<br>σ=0.15, κ=0.05 | 低耦合（只读/可切分）<br>σ=0.10, κ=0.01 |
|---|---|---|
| 1 | 1.00× | 1.00× |
| 2 | 1.60× | 1.79× |
| 4 | **1.95×（峰值）** | 2.82× |
| 8 | 1.65× | 3.54× |
| 9~10 | — | **3.57×（峰值）** |
| 16 | 1.05× | 3.27× |
| **N_max** | **√17 ≈ 4.1** | **√90 ≈ 9.5** |

**这张表直接把初稿的定性建议变成了可计算的量**：写入型任务峰值在 K≈4，只读型任务峰值在 K≈9~10。初稿说的「写入 2~4、只读 8~16」与 USL 推导一致。

> **【2026-09-27 更正，勿再按本句取数】** 上表两个 N_max 都是从**猜的** σ/κ 推出来的，本节当时把它当成"可计算"，其实是"可推导"——推导不检验输入。真实派发实测（速查 §16、§18、§19）：写入档 σ=0.697 / κ=0.069 / **N_max=2.09**（不是 4.1）；只读档 30 个臂识别不出 κ，且 N=2~12 **没有一档跑赢串行**，"9~10"只是 `√((1−0.10)/0.01)` 的算术结果，从未被观测支持。保留本句是因为它记录了一次典型失误：**把公式的形状当成世界的形状**。

### 1.4 一个跨领域的巧合（值得注意）

三个**互相独立**的领域给出了同一个二次结构：

| 来源 | 领域 | 结论 |
|---|---|---|
| Gunther USL (1993) | 排队论/容量规划 | 一致性开销 ∝ N(N−1) |
| Brooks 法则 (1975) | 软件工程管理 | 收益 O(N)，协调+合并成本 O(N²) |
| Kim et al. (2026) | LLM 多智能体实测 | 回合数 ∝ N^1.724，R²=0.974 |

初稿的直觉是对的，只是缺了这层数学背书。

---

## 2. 核心实验证据：Nature Machine Intelligence (2026)

**文献**：Kim, Y. et al. *Capable language models can outgrow the benefits of collaboration*. Nature Machine Intelligence 8, 1157–1172 (2026). DOI: 10.1038/s42256-026-01268-y，2026-07-24 在线发表。

**设计**：260 个配置，6 个 benchmark，5 种架构（single / independent / centralized / decentralized / hybrid），3 个模型家族；**严格控制任务提示、工具接口、系统级算力预算**（平均推理预算约 4800 tokens）。

### 2.1 可分解性决定成败，而非复杂度

| 任务 | 最佳多 Agent 变化 | 为什么 |
|---|---|---|
| Finance Agent | **+80.8%**（中心化） | 监管新闻/公司文件/运营影响/市场数据 = 天然独立分支 |
| PlanCraft（Minecraft 规划） | **−70.0%**（独立） | 前一步直接改变后一步环境状态，无可并行分支 |
| BrowseComp-Plus | +9.2% | 搜索类，可切分 |
| WorkBench | +5.6% | — |
| SWE-bench Verified | 四种结构**全部小幅下降** | 编码可并行部分少 |
| Terminal-Bench | 独立 +6.0% / 中心化 **−20.0%** | 共享 shell 状态 |

**Finance Agent 复杂度评分 0.407，PlanCraft 0.419 —— 几乎相同，结果却完全相反。**
→ **复杂度分数不足以决定是否组队**。真正决定因素是：子任务能否并行、各分支是否依赖同一份持续变化的状态、中间结果能否被可靠验证。

### 2.2 协作税（Coordination Tax）—— 实测数字

| 架构 | 平均回合数 | vs 单 agent 开销 | 每 1000 token 成功数 |
|---|---|---|---|
| 单 agent | 7.2 | — | **67.7** |
| 独立（无通信） | 11.4 | +58% | 42.4 |
| 去中心化 | 26.1 | +263% | 23.9 |
| 中心化 | 27.7 | +285% | 21.5 |
| 混合 | 44.3 | +515% | 13.6 |

混合架构通信最多，成功率 0.452，**反而低于单 agent 的 0.466**。

### 2.3 验证机制是最大单一杠杆

| 结构 | 轨迹级错误放大系数 | 协调失败率 |
|---|---|---|
| 独立（无中心验证） | **17.2×** | — |
| 中心化（orchestrator 汇总前核对） | **4.4×** | 1.8% |
| 去中心化 | — | 3.2% |
| 混合 | — | 12.4% |

**中心化验证把错误放大压低了近 4 倍。** 中心化/去中心化平均减少 22.7% 事实错误（金融任务最高 31.4%）。

另有冗余度甜点：成功运行的中位冗余率约 0.41；**冗余率超过 0.50 后与成功率负相关**。矛盾 token 占比：成功运行中位 2.3%，失败运行 8.1%。

### 2.4 45% 阈值的证据与边界（必须诚实标注）

- 单 agent 基线是唯一同时通过**聚类稳健推断 + Holm–Bonferroni 多重比较校正**的单项预测因子。
- 「单 agent 基线 × agent 数量」拟合出约 **45%** 的边界：基线低于此线，多 Agent 更可能正收益；高于此线，收益归零或转负。
- 在 SWE-bench Verified 与 Terminal-Bench 的 16 个「模型 × benchmark」配置上方向命中 **15/16 = 94%**。
- 模型五折交叉验证 R² = 0.373（用任务内能力指标替换后 0.413），能在 87% 的域内留出配置中选对最佳架构。

**边界（作者自己强调，不可忽略）**：
1. 生成 45% 的交互项**未通过聚类稳健校正**；6 个 benchmark 只构成 6 个数据集簇。作者明确称之为「经过验证的选择规则，不是通用扩展定律」。
2. SWE-bench Verified / Terminal-Bench 每个配置**只测 20 个实例**，单配置 bootstrap 置信区间约 ±20 个百分点。
3. **留出整个任务域后 R² = −2.09** —— 跨域绝对预测失效，必须在目标域内重测。
4. 论文由 Google LLC 资助，多位作者为 Alphabet 员工/持股。

→ **正确用法**：把它当作「要不要先试多 Agent」的**域内启发式**，不是普适定律。

---

## 3. 失效模式证据：MAST（UC Berkeley, NeurIPS 2025）

**文献**：Cemri, M. et al. *Why Do Multi-Agent LLM Systems Fail?* arXiv:2503.13657v3。1,642 条真实执行轨迹，7 个主流开源 MAS（MetaGPT、ChatDev、HyperAgent、AppWorld、AG2、Magentic-One、OpenManus），Cohen's κ = 0.88。

**整体失败率 41% ~ 86.7%。**

### 3.1 三大类别与 14 种模式（含流行度）

**FC1 系统设计/规格问题（合计 ≈44.2%）—— 第一大类别**
| 代码 | 模式 | 流行度 |
|---|---|---|
| FM-1.3 | Step repetition 步骤重复 | **15.7%** |
| FM-1.5 | Unaware of termination conditions 不知何时收尾 | **12.4%** |
| FM-1.1 | Disobey task specification 违反任务规格 | **11.8%** |
| FM-1.4 | Loss of conversation history 上下文丢失 | 2.80% |
| FM-1.2 | Disobey role specification 违反角色规格 | 1.50% |

**FC2 智能体间失配（合计 ≈32.4%）**
| 代码 | 模式 | 流行度 |
|---|---|---|
| FM-2.6 | Reasoning-action mismatch 推理与行动不一致 | **13.2%** |
| FM-2.3 | Task derailment 任务跑偏 | 7.40% |
| FM-2.2 | Fail to ask for clarification 不主动澄清 | 6.80% |
| FM-2.1 | Conversation reset 对话重置 | 2.20% |
| FM-2.5 | Ignored other agent's input 无视他人输入 | 1.90% |
| FM-2.4 | Information withholding 信息扣留 | 0.85% |

**FC3 任务验证（合计 ≈23.5%）**
| 代码 | 模式 | 流行度 |
|---|---|---|
| FM-3.3 | Incorrect verification 错误验证 | 9.10% |
| FM-3.2 | No/incomplete verification 无/不完整验证 | 8.20% |
| FM-3.1 | Premature termination 过早终止 | 6.20% |

### 3.2 与初稿建议的直接对应

| 初稿建议 | MAST 对应失效模式 | 干预实测收益 |
|---|---|---|
| **P0-1 契约先行** | FM-1.1 (11.8%) + FM-1.2 (1.5%) | 仅修角色规格 → **+9.4%** 成功率 |
| **P0-3 只读才高并发** | FM-1.3 Step repetition (15.7%) | 并行分支互不知情 → 重复劳动，正是碎片化的直接症状 |
| **验收门禁前置**（初稿列为 P1） | FM-1.5 (12.4%) + FM-3.1/3.2/3.3 (23.5%) | 增加高层目标验证步骤 → **+15.6%** 成功率（论文中最大单项改进） |
| **P0-4 结构化输出** | FM-2.6 (13.2%) | 减少推理-行动不一致 |

**注意**：论文还发现，现有验证器往往只做表面检查（"能编译吗""有没有残留 TODO"）——一个 ChatDev 生成的国际象棋程序通过了表面检查，但因未按真实规则验证仍有运行时 bug。**这直接支持初稿"验收命令前置、要求子 agent 自证通过"的主张。**

**适用边界**：MAST 的轨迹来自**对话式/编排式 MAS**（agent 之间多轮协商），与"主 agent 派发 K 个独立子任务"的扇出式并行不完全等同。它是目前最好的失效分类法，但外推时需注意。

---

## 4. 上下文侧：初稿最重要的一处修正

### 4.1 已证实的部分

- **Anthropic《Effective context engineering for AI agents》**：context 必须被视为**具有边际收益递减的有限资源**；LLM 有"注意力预算"；transformer 中 n 个 token 产生 n² 两两关系，上下文变长时捕捉关系的能力被摊薄。
- **Chroma Research《Context Rot》(2025-07-14)**：测试 18 个前沿模型，**全部**随输入变长而退化（含 GPT-4.1、Claude Opus 4、Gemini 2.5 Pro、Qwen3-235B）。这是 transformer 注意力的架构属性，非能力缺陷。
  - 补充发现：干扰项越多退化越快；**逻辑连贯的文档反而比打乱的 haystack 表现更差**（连贯文本产生更"像真的"干扰项）。
  - LongMemEval：聚焦 ~300 tokens 与完整 ~113K tokens 之间存在显著准确率差距。
- **lost-in-the-middle**（Liu et al., Stanford/TACL 2024）：20 文档 QA 中，相关信息置于位置 5–15 时准确率下降 30%+（位置 1 约 75%，中间约 45–55%）。

→ **初稿"先让上下文变小，再谈并行"的主张得到强支持。**

### 4.2 必须修正的部分

初稿写「背景上下文复制 K 份 = **纯浪费**」。这个措辞错了。

**Stanford (Tran & Kiela, arXiv:2604.02460, 2026-04)**：在**固定 thinking-token 预算**下（100~10,000 tokens），单 agent 在多跳推理上持续匹配或超过多 agent 系统（Qwen3 / DeepSeek-R1-Distill-Llama / Gemini 2.5，FRAMES + MuSiQue 4-hop）。理论依据是**数据处理不等式**：每次 handoff 都是有损压缩，后处理只能丢信息、不能加信息。

但论文同时给出多 Agent **确实会赢**的两个条件，而这正是关键：
1. **单 agent 的上下文利用被降级时**（问题装不进窗口 / 模型处理长上下文能力差 / 上下文被污染）
2. **愿意花更多算力时**

**Anthropic 的架构说明也印证**：子 agent"各自拥有独立上下文窗口并行探索，再把最重要的 token 压缩给主 agent"，提供 **separation of concerns**（关注点分离），减少路径依赖。其数据：BrowseComp 评估中 **token 使用量本身解释 80% 的性能方差**，三因素合计解释 95%。

→ **修正后的正确表述**：
> 上下文复制是一笔**昂贵的交易**，不是浪费。它买到的是「隔离 + 并行注意力预算 + 突破单窗口容量」。是否划算，取决于**单 agent 的上下文是否已经被污染或撑满**——这正是 Tran & Kiela 的条件 (1)，也是 Anthropic 所说的"信息超出单一上下文窗口"场景。
> 因此判断顺序是：**先测单 agent 在干净上下文下能做到什么；只有当它做不到是因为"装不下/被污染"时，才用并行换隔离。**

---

## 5. 支持并行的一面（避免只挑有利证据）

| 证据 | 数字 | 来源 |
|---|---|---|
| 多 Agent 研究系统 vs 单 agent | **+90.2%**（内部 research eval） | Anthropic, 2025-06 |
| 金融分析任务（中心化多 Agent） | **+80.8%** | Nature MI 2026 |
| 事实错误减少 | 平均 −22.7%，金融任务最高 −31.4% | Nature MI 2026 |
| 子 agent 规模 | Anthropic 生产系统实际 **3~5 个** | Anthropic 工程博客 |
| Gemini-2.0 Flash 团队规模扫描 | 峰值出现在 **~7 个** agent | Nature MI 2026 |

**成本**：agent 约 4× chat token，多 agent 约 **15×** chat token（Anthropic 实测）。
**Anthropic 自己划的适用边界**：多 Agent 适合"高度可并行、信息超出单一上下文窗口、需与大量复杂工具交互"的任务；**编码任务可并行部分少，且 LLM agent 尚不擅长实时协调与委派**。

**Cognition（Devin 团队）立场**："actions carry implicit decisions, and conflicting decisions carry bad results"——拆分上下文的并行 worker 会做出互相冲突的隐式决策；Devin 采用单线程 agent + 上下文压缩。

---

## 6. 修订版决策规则

```
Step 0  测单 agent 基线（干净上下文、充足预算）
        └─ 基线 > ~45%？ → 默认单 agent，除非有明确隔离/容量需求
        └─ 基线 < ~45%？ → 进入 Step 1

Step 1  可分解性检查（Nature MI：复杂度分数无效，看这三条）
        ├─ 子任务能否同时推进？            否 → K=1
        ├─ 各分支是否依赖同一份持续变化状态？ 是 → K=1 或 K=2（强契约）
        └─ 中间结果能否被独立验证？          否 → K=1

Step 2  定 K（用 USL，不要拍脑袋）
        K_target = √((1 − σ) / κ)
        ├─ 只读 / 独立分片 / 有验收：σ≈0.10, κ≈0.01 → K_max ≈ 9
        └─ 写入 / 共享状态 / 跨模块：σ≈0.15, κ≈0.05 → K_max ≈ 4

Step 3  强制中心化验证（不可省略）
        └─ 无验证：错误放大 17.2×；有中心协调器：4.4×

Step 4  定 briefing 冗余率
        └─ 目标 ≈0.41（中心化结构中位数）；>0.50 与成功率负相关
```

---

## 7. 修订版 P0 清单（按影响 × 收益）

| # | 动作 | 量化依据 | 相对初稿的变化 |
|---|---|---|---|
| **P0-1** | 契约先行（`contract.md`：接口 schema / 命名表 / 禁写清单 / 验收命令） | MAST 规格类失败 44.2%；修角色规格 +9.4% | 保持 P0，证据升级 |
| **P0-2** | **中心化验证门禁**（每个子 agent 自证通过命令级验收才回传） | 错误放大 17.2× → 4.4×；MAST 高层验证 +15.6% | **从 P1 升级为 P0** |
| **P0-3** | 写入按文件 owner 互斥分片，K≤4 | ~~USL 高耦合 N_max≈4~~ → **实测 N_max=2.09**（σ=0.697 / κ=0.069，9 臂两轮，速查 §16），K≤4 偏宽；Anthropic 用 3~5 是**质量**口径，与本行的墙钟口径不同 | 上限按实测收到 **K=2** |
| **P0-4** | 只读才允许高并发，K≤~9 | ~~USL 低耦合 N_max≈9.5~~ → **2026-09-27 实测否定其墙钟含义**：30 个臂无一档跑赢串行；K≤~9 只在"这件事串行本来就要 ≥12~14 分钟"时成立（速查 §18、§19） | 数值从"8~16"收窄为"≤9"，**再收窄为一个条件而非一个数** |
| **P0-5** | **先测单 agent 基线，对照 45% 阈值** | Nature MI，16 配置命中 94% | **新增** |
| **P1-6** | 结构化输出 schema，主 agent 直接拼装 | FM-2.6 13.2% | 保持 |
| **P1-7** | 共享背景 briefing 冗余率控在 ~0.41 | Nature MI 冗余甜点 | **新增** |
| **P1-8** | 自适应并发熔断（429/失败率自动降 K） | 实测回合数 ∝ K^1.724 | 保持，证据升级 |
| **P1-9** | 增量 commit / 独立分支 | 工程实践 | 保持 |

---

## 8. 落到具体项目

**BRAIN R-10（实时推送）与 R-12（图表）**
两者共享 V5-025 双端兼容约束 + R-09 前端拆分产出 → **共享状态 + 跨模块一致性**，对应 USL 高耦合档（κ≈0.05）→ **K_max ≈ 4，实际建议 K=2**。
且必须先行产出两份契约：事件 payload schema、图表数据接口。
⚠️ 注意 Anthropic 明确指出**编码类任务可并行部分少**，这两个任务本质是编码，风险偏负。

**BRAIN / EWOH 大规模代码审计（580+ 文件）**
- 扫描阶段：只读、按目录分片 → 低耦合档，~~**K=8~9 合理**~~ **2026-09-27 收窄：K=8~9 只在"这一遍扫描串行本来就要 ≥12~14 分钟"时合理**（每个 agent 固定开销实测 ≈50.3 秒，扇出跨度取 K 个耗时的最大值，速查 §19）。按 580+ 文件、每目录 20~50 文件的粒度分片**大概率**够这条门槛（**未实测**：EWOH 那一遍扫描串行多久没有读数，手上只有 worker 侧约 50 秒的固定开销）。**按"每片一两个文件"分则一档都不划算**——我们那 30 个臂就是这个反面样本。
- 消化/写入阶段：**K=2~3**
- 中间必须有主 agent 综合 + 中心化验证环节（不能让子 agent 直接产出最终报告）
- 严禁 16 并发全写 —— 与既有 429 失败经验一致

---

## 9. 信源质量说明（诚实标注）

| 来源 | 类型 | 可信度 | 备注 |
|---|---|---|---|
| Nature MI (Kim et al. 2026) | 顶刊同行评审，受控实验 | **高** | 但由 Google 资助；45% 阈值交互项未过聚类稳健校正；SWE-bench 每配置仅 20 实例 |
| MAST (arXiv:2503.13657) | 顶会（NeurIPS 2025），1,642 真实轨迹 | **高** | 轨迹来自对话式 MAS，与扇出式并行不完全等同 |
| Tran & Kiela (arXiv:2604.02460) | 预印本，受控等预算 | **中高** | 理论含"完美上下文利用"理想化假设；仅限多跳推理，SWE/工具使用不在范围 |
| Anthropic 工程博客 | 厂商一手工程披露 | **中高** | 90.2% 是**内部 eval**，不可外推为行业基准 |
| Chroma Context Rot (2025-07) | 厂商研究，18 模型 | **中高** | 结论方向被 Anthropic 独立复述 |
| USL / Brooks | 经典理论，跨领域复用 | **高**（理论） | 用于 LLM agent 属**类比外推**，非直接测量 |

**主动排除的信源**：检索中出现若干内容农场页面（如 pandev-metrics.com、aictrl.dev 等），其给出的"SWE-bench 38%→62%→71%→54% 随 agent 数变化"等表格**无法追溯到任何一手研究**，呈现明显的合成内容特征，**本报告不予采用**。若需引用此类数字，须先定位原始论文。

---

## 10. 一句话结论

初稿的方向正确、机制描述偏乐观且不完整。修正后：

> **并发度上界由公式决定（N_max = √((1−σ)/κ)），不是由任务数量决定；进入并行前先测单 agent 基线并对照 45% 阈值；进入后必须有中心化验证，否则错误放大 17.2×。上下文复制是贵，不是浪费——只有当"装不下或被污染"成立时，这笔交易才划算。**

---

## References

- [Capable language models can outgrow the benefits of collaboration — Nature Machine Intelligence 8, 1157–1172 (2026)](https://www.nature.com/articles/s42256-026-01268-y)
- [Capable language models can outgrow the benefits of collaboration — MIT Media Lab publication page](https://www.media.mit.edu/publications/capable-language-models-can-outgrow-the-benefits-of-collaboration)
- [Why Do Multi-Agent LLM Systems Fail? (MAST) — arXiv:2503.13657v3](https://arxiv.org/html/2503.13657v3)
- [Why Do Multi-Agent LLM Systems Fail? — 项目主页](https://sites.google.com/berkeley.edu/mast)
- [Single-Agent LLMs Outperform Multi-Agent Systems on Multi-Hop Reasoning Under Equal Thinking Token Budgets — arXiv:2604.02460](https://arxiv.org/abs/2604.02460)
- [How we built our multi-agent research system — Anthropic Engineering](https://www.anthropic.com/engineering/built-multi-agent-research-system)
- [Effective context engineering for AI agents — Anthropic Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Context Rot: How Increasing Input Tokens Impacts LLM Performance — Chroma Research](https://research.trychroma.com/context-rot)
- [Neil J. Gunther — Universal Scalability Law (Wikipedia)](https://en.m.wikipedia.com/wiki/Neil_Gunther)
- [Universal Scalability Law — R usl package vignette](https://www.stats.bris.ac.uk/R/web/packages/usl/vignettes/usl.pdf)
- [Scalability in Computing and AI — arXiv:2006.04969](https://arxiv.org/pdf/2006.04969v1)
- [Concurrency vs. Throughput: why more parallelism can make databases slower — PlanetScale](https://planetscale.com/blog/concurrency-vs-throughput-vitess-mysql)
- [Brooks's law — Wikipedia](https://en.wikipedia.org/wiki/Brooks%27_Law)
- [Context Rot: The Complete Guide — MorphLLM](https://www.morphllm.com/context-rot)
- [Research Roundup: Multi-Agent Coding Makes Results Worse — AGI Hunt（二手汇编，仅用于线索发现）](https://agihunt.info/en/p/1a06ed1fd4efa21ab8c295f2b8c)

---

# 补充章节：隔离机制与长时程任务（2026-09-09 二轮研究）

## CAID（CMU / OpenHands）

- **文献**：Jiayi Geng, Graham Neubig, *Effective Strategies for Asynchronous Software Engineering Agents*（arXiv:2603.21489）。一手来源为 OpenHands 官方博客上作者本人撰写的客座文章。
- **架构**（Centralized Asynchronous Isolated Delegation）：中心 manager 构建依赖图 DAG → 每个 engineer 在独立 **git worktree** 中异步执行 → 自跑测试通过后 commit → manager 执行 **git merge**（冲突由产生该 commit 的 engineer 自行解决）→ 测试门控合并，main 始终处于可用状态。manager 与 engineer 之间用**结构化 JSON** 通信，而非自由对话。
- **结果**：PaperBench **+25.6%**、Commit0-Lite **+14.7%**（绝对提升，基于最弱模型 MiniMax 2.5），在 Claude 4.5 Sonnet、GLM 4.7、MiniMax 2.5 三个模型上方向一致。
  > ~~PaperBench +26.7% / Commit0 +14.3%~~ 为早期二手转载值，已按一手 Table 2 改正（见本文件末「更正」节）。
- **关键消融（本轮最重要的发现）**：**软隔离**（仅用 prompt 声明各 agent 改不同文件）在 PaperBench 上**表现差于单 agent**；换成 git worktree **物理**隔离后才转为正收益。
- **收益强弱不对称**：PaperBench 分模型看 —— ~~MiniMax 2.5：10.4% → 36.7%（+26.3）~~；Claude 4.5 Sonnet：57.2% → 63.3%（**+6.1**）；GLM 4.7：38.0% → 45.4%（+7.4）。
  > **更正（2026-09-15，手工核对 Table 2）**：MiniMax 2.5 的一手数值为 **10.5% → 36.1%（+25.6）**，本节原先的 10.4%→36.7%（+26.3）来自二手转载。已按一手改正；`关键数字速查.md` §9 与 §15 同步。保留划线而非静默删除，因该数字曾被引用到 SKILL.md。
  → **独立佐证 Nature MI 的「能力饱和」**：模型越强，并行带来的收益越小。两篇独立论文指向同一规律。
- 单 agent 给**更多迭代**会平台化（反复回看、改坏已可用代码，额外算力大部分浪费）；多 agent 仍能继续受益，因为子任务确实可并行推进。

## 与 Nature MI 的表面矛盾如何调和

| | Nature MI（SWE-bench Verified） | CAID（Commit0 / PaperBench） |
|---|---|---|
| 任务时程 | 短（单个 issue 修复） | 长（从零建库、复现论文） |
| 隔离机制 | 共享 Docker 环境，**无 worktree** | git worktree 物理隔离 |
| 结果 | 四种多 agent 结构**全部小幅下降** | **+14.7% / +25.6%**（一手值；早期二手转载为 +14.3% / +26.7%）|

→ **决定因素不是「编码 vs 研究」，而是「任务时程 × 隔离机制」。**
短时程 + 无隔离 → 不要并行；长时程 + worktree 物理隔离 → 可并行且收益显著。

## 与 Tran & Kiela 的表面矛盾如何调和

- Tran & Kiela：**同等**推理预算下，单 agent 在多跳推理上持平或胜出。
- CAID：给单 agent **更多**迭代会平台化，多 agent 仍继续受益。
- 二者不矛盾：预算**相同**时单 agent 更省（Tran & Kiela）；预算**增加**时单 agent 边际收益趋零而多 agent 仍能扩展（CAID）。
- 决策含义：**先问「我要加的是预算还是并行度」**——加预算先给单 agent，加并行度才用多 agent。

## 信源说明（诚实标注）

- CAID 数字以 OpenHands 官方博客（作者本人撰写）为准；emergentmind、alchemictechnology、CSDN 均为二手转载。
- **口径差异需注意**：头条值（一手 **+25.6%**，曾被二手误传为 +26.7%）是**基于最弱模型 MiniMax 2.5** 的聚合/最大提升，**不是跨模型通用值**；分模型看 Claude 4.5 Sonnet 在 PaperBench 上仅 **+6.1**。引用时必须带模型。
- ~~「软隔离差于单 agent」尚未核对原论文~~ → **已于第三轮核对一手论文（Table 3）并修正**：软隔离并非一律差于单 agent。PaperBench 上 55.5% < 单 agent 57.2%；Commit0-Lite 上 56.1% > 单 agent 53.1%，但仍显著低于 worktree 的 59.1%。

---

## 更正（2026-09-09，第六轮）

本报告第一节曾写：「与用户 EWOH 审计实测经验一致：16 并发触发 429 失败，改『4 扫描 + 8 消化』跑通」——暗示该经验佐证了 USL 的协调成本峰值。

**这是一个分析错误，混淆了两类失败。**

- 用户的 16 并发失败是 **429 限流**（供应商容量上限），**不是**协调成本导致的返工或质量下降。
- 两者都表现为「K 太高跑不动」，但机理不同：限流是外部容量约束，**没有公式可算、只能实测探测**（AIMD）；协调成本是内部串扰，可用 `N_max=√((1−σ)/κ)` 建模。
- 因此该经验**不能**作为 USL 峰值的实证支持。它支持的是另一条结论：**固定并发是错的，应改用 AIMD 自适应**。

正确处理：**有效 K = min(K_协调最优, K_限流上限)**，先分清是哪一个再开药方。详见 `关键数字速查.md` §10（限流 vs 协调成本，两类失败别混）。

（保留此更正而非静默修改，是因为该错误曾被当作结论引用过。）

---

## 2026-09-28 调研：开源「并行子 agent」实现里，谁已经把「请求的 K」与「真正跑起来的 K」分开记账

**这次问的问题**：不是"拟合用哪个库"（§12、§17 已答），而是**扇出点到底该怎么记账**。本仓库被同一类事绊了三次：速查 §18「平台把臂静默串行化」、速查 §20 结果四「请求 18 个、实测只有 17 个同时在跑」、速查 §22 ①「派发消息与简报给了两个取钟时刻」＋ §22 ⑬「两条臂共用一个身份，后一条把前一条从磁盘上吃掉」。这四件在成熟实现里有没有现成接法——先去读源码，不在第二轮里自己发明第二遍。

**检索面与一手性**：repo 元数据取自 `https://api.github.com/repos/<owner>/<repo>` 的 JSON 回包（本轮逐条打印过 `stargazers_count / license.spdx_id / pushed_at`）；源码与 README 一律走 `raw.githubusercontent.com` 逐字重开；`topic:multi-agent` + `topic:orchestration` 检索 total=1558。**星数、许可、活动三列是一手读数；「并行的到底是什么」一列是我亲手读到的源码位置，不是转述。**

**候选与可复用性**（六个维度，URL 本轮真实访问）

| 方案 | 功能匹配度 | 许可 | 维护活跃度 | 安全风险 | 代码质量 | 能否复用 |
|---|---|---|---|---|---|---|
| [bytedance/deer-flow](https://github.com/bytedance/deer-flow)（83,050★）| **最高**：N 个子 agent 在同一个任务内并发，且扇出点有准入控制器 | MIT | pushed **2026-09-27**，很活跃 | 低（沙箱＋lease，见 §22 的隔离讨论）| 高：容量控制器/中间件/持久化/控制测试分得很清 | **借语义**（后端太重，不能引依赖）|
| [google/adk-python](https://github.com/google/adk-python)（21,663★）| 高：`ParallelAgent` 是一等公民的 fan-out/fan-in 原语 | Apache-2.0 | pushed **2026-09-26** | 低 | 高：TaskGroup 语义与 pre-3.11 兜底分开写并有注释 | **借形状**（扇入哨兵＋背压），无 N 上限、不测墙钟 |
| [microsoft/agent-framework](https://github.com/microsoft/agent-framework)（13,824★）| 中：`ConcurrentBuilder` 是工作流级 fan-out/fan-in＋聚合器 | MIT | pushed **2026-09-27** | 低 | 中高：检查点/流式/HITL 齐全，但没有并发上限 | 抄思路（聚合侧，不是测量侧）|
| [BloopAI/vibe-kanban](https://github.com/BloopAI/vibe-kanban)（28,205★）| 中：并行的是**每张卡一个 worktree 的多个编码 agent**，不是单任务内分片 | Apache-2.0 | pushed **2026-09-19** | 中（要跑他人 command）| 中高：worktree 创建有逐路径锁＋重试＋清理 | 借语义 1 条（provision 不是免费）|
| [obra/superpowers](https://github.com/obra/superpowers)（292,165★）| 低：**只有散文判据**，无计时、无上限、无准入 | MIT | pushed **2026-09-27** | 低 | 中（技能文档，非可执行装置）| 不能复用——但它独立写着我们那条派发纪律 |

**决定：借语义，不引依赖。** 理由：deer-flow 这套是 FastAPI＋SQLModel＋LangGraph 后端（`backend/app/gateway/routers/subagents.py`、`persistence/subagent_batches/sql.py` 都在场），而本技能包按上下文预算装载、拟合线是 stdlib-only，塞不进；能带走的只有**接口形状**。下面六条每条都指回它的原文出处，并指到它在我们装置里要改的接线位。**本轮不动 `kappa-measure/` 的装置**——那台机器上另一个会话正在投臂，速查 §22 ⑩／⑬ 记的就是"在别人正在用的档案上动手会把证据吃掉"；这六条与既有的待办「两把尺共用一条臂资格规则」「R3 把横轴换成交付并发度」同批实施。

1. **先钳成"准入的 K"，再把队列深度记进行里** ← deer-flow `backend/packages/harness/deerflow/subagents/capacity.py`：`SubagentCapacitySnapshot(max_running, running, max_queued, queued, admission_policy)`，FIFO waiter，类文档原话 *"FIFO async capacity controller; queued work never owns a thread"*；缺省值在 `config/subagent_runtime_config.py:11-28` ⇒ `max_running=3 / max_queued=64 / admission_policy="queue" / queue_timeout_seconds=300`；`config/subagents_config.py:12-16` ⇒ 每轮总量缺省 **6**、硬上限 **50**、并发钳位 **[1,64]**，且 `clamp_subagent_concurrency(value, execution_capacity=...)` 把**请求值钳到执行容量**。我们手上只有事后的 `concurrency_observed` 加整臂作废。落点：`ro_measure.py:prep/collect` 记一份准入快照（running/queued）进臂状态。
2. **准入失败是一种终点，不是"这臂慢"** ← 同一文件 `subagents/executor.py:1389-1400`：`async with capacity.slot():` 之内才 `result.status = RUNNING` 并 `result.started_at = _utcnow()`；`except SubagentCapacityError` 走 `try_set_terminal(FAILED, ..., admission_failure=True)`，而 `executor.py:138` 给这个字段写了语义（*"Whether capacity rejected/timed out before execution started"*）。对照：一支从没拿到槽位的臂与一支干得慢的臂，在我们 12 列记账行里同形，只能靠 cov 反推。落点：记账行新增 `admission=`，`ro_fit` 的门槛按**拒因**分流，而不是按 cov 一刀切。
3. **取钟权在编排器手里** ← `executor.py:1392-1394` 的 `started_at` 由编排器在拿到槽位那一刻打；README（1663 行区）另有 *"completed sub-agent usage is attributed back to the **dispatching step** from that run's terminal tool-message metadata rather than a process-global provider-ID cache"*。对照：速查 §22 ① 量到两个取钟时刻中位差 **40.3 秒**（与单 worker 固定开销同量级），pin 档只把权威收归简报，没把"拿到槽位的时刻"做成一个字段。落点：`collect` 记准入时刻，跨度端点由它给，worker 自报降为交叉核对。
4. **并发跑之间的身份要由服务端铸造，不能借 provider 的 ID** ← README（1663 行区）原话：*"Concurrent parent runs also receive independent server-side sub-agent execution IDs, so a provider that reuses a tool-call ID cannot make one run poll, cancel, or clean up another run's background task."* 这正好是速查 §22 ⑬（未盖戳的两条同并发度臂共用一个归档名，后一条把前一条冲掉）＋ §22 ⑩（4 个槽位里 2 个执行了别会话的任务书）两件事的合起来形状。前一件我们已用 `arm_key` 修，**后一件没有对应物**——手上只有 `cohost=` 协变量，没有"这个载荷是不是我的"的判据。落点：`prep` 铸造 nonce 写进简报，`collect` 要求 worker 回显；不回显即拒，拒因写"载荷归属未证实"而不是"平台串行化"。
5. **扇入的完成定义＝每支一个哨兵；每支的首事件延迟是可观测量** ← adk-python `src/google/adk/agents/parallel_agent.py` 的 `_merge_agent_run`：`asyncio.TaskGroup` 起 N 支、单一 `asyncio.Queue` 收，每支结束时 `queue.put((sentinel, error))`（哨兵自带那一支的错误），主循环 `while sentinel_count < len(agent_runs)`；每个事件配一个 `resume_signal` 做**背压**（上游没消费就不产下一事件），直接子 agent 的 escalate 会 `_cancel_tasks(tasks)` 收掉其余分支；分支隔离用 `_BranchPath.create_sub_branch`。我们的跨度是 `max(t_end) − min(t_start)`，起跑错峰只有一个聚合数（速查 §20 的"抖动占比"）。借的是形状：**把每支首个可见事件的时刻单独记下来**，错峰就从"臂的噪声"变成可分列的一项。注意 ADK 对 N **没有上限**，也不测墙钟——它防的是"把队列当算法"，不回答"K 该开多大"。
6. **隔离机制自己也带争用，别把它当免费** ← vibe-kanban `crates/worktree-manager/src/worktree_manager.rs:95-163`：每个 worktree 路径单独一把 `WORKTREE_CREATION_LOCKS` 异步锁，创建前做 "comprehensive cleanup"，创建步注释写明 *"retry logic for metadata conflicts"*，清理函数注释写明防的是 *"path exists"* 类错误。对照：速查 §16 把合并秒数 11.7→111.7 的巨大噪声归给"卷/git 的暖机开销"就停了，N=12 那支 merge 占跨度 37%。借法：写侧把 **provision 与扇出窗口分开计时**（`provision_seconds` 单列），别让 N 个 agent 抢同一份 `.git/worktrees` 元数据的串行化混进 κ。

**顺带一条独立佐证**（deer-flow README 1663 行区，讲 lead agent 何时才许扇出）：*"…when delegation has clear net benefit from real parallel latency, specialist capability, or context isolation. It keeps interdependent scopes and overlapping side effects out of parallel dispatch… The lead uses the fewest useful sub-agents and re-evaluates later batches instead of fanning out solely because a task is large or multi-step."* ——一个 83k★ 的生产实现独立收敛到"最少可用子 agent ＋ 互斥副作用不进并行"，与我们速查 §2 的软/硬隔离消融、§11 的 agentic drift 同向，但**它给的是策略不是数**。

**方法论借鉴（不引代码但改了流程的）**：deer-flow `agents/middlewares/subagent_limit_middleware.py` 把"这一轮还能不能再派"做成**运行时中间件**，总量钳在 [1,50]、并发钳在 [1,64]，超限还给模型一句可读的收尾指令（`_TOTAL_LIMIT_STOP_MSG`：用已收回的子 agent 结果／简单活儿自己做／改写为总结，别再派）。我们的 K 上限到今天还是给人看的表，不是会拦住第二次派发的东西——这条差异是本档案 §7 P0-3/P0-4 与真实执行之间的一个洞。

**未找到（这也是结论）**
- `api.github.com/repos/ruvnet/claude-flow` 与 `api.github.com/repos/strands-agents/sdk-python` **两条都回 404**（各试过两次，回包 `full_name=null`）。常被点名的两个"hive-mind / `Parallel` 代理类型"实现，本轮**未检索到**，因此本报告不引用它们，也不拿相近内容补位。
- 五个真实访问过的实现里，**没有任何一个把 σ/κ、N_max 或墙钟加速曲线做进产品**：deer-flow 的 cap 是运维安全值（缺省 `max_running=3`、每轮总量 6）而非拟合出的峰值；ADK 与 agent-framework 只给 fan-out/fan-in 形状；superpowers 只有散文判据（`skills/dispatching-parallel-agents/SKILL.md:68-77`：*"Multiple dispatch calls in one response = parallel execution. One per response = sequential."*，无 K 数、无计时）。⇒ 与 §17 那句"未检索到把 USL 拟合用在 LLM agent 团队上的公开工作"一致：速查 §16 那 9 臂仍是**自有实测**，别写成行业基准。

**只做了元数据核、没读源码（导航档，不算结论）**：[smtg-ai/claude-squad](https://github.com/smtg-ai/claude-squad) 8,537★／AGPL-3.0／pushed 2026-08-20、[Untrivial-ai/agent-orchestrator](https://github.com/Untrivial-ai/agent-orchestrator) 12,423★／Apache-2.0、[chaitanyagiri/munder-difflin](https://github.com/chaitanyagiri/munder-difflin) 8,056★／MIT、[camel-ai/camel](https://github.com/camel-ai/camel) 17,784★／Apache-2.0（README 自述社区目标是 *"finding the scaling laws of agents"*，但其"scaling law"指行为与涌现，不是墙钟；本轮未读其 team/workforce 源码，故不据它下结论）、[mco-org/mco](https://github.com/mco-org/mco) 526★／MIT、[josstei/maestro-orchestrate](https://github.com/josstei/maestro-orchestrate) 462★／Apache-2.0（自述"39 specialists, parallel subagents"）。这些的星数与许可同样是 API 一手回包，但"并行的到底是什么"未经源码核实。

**本轮调研没有改动的东西（照实写）**：没动任何阈值、判据线、记账行形状或 SKILL.md 的 K 表——上面六条的落点全在另一会话正在使用的 `kappa-measure/` 装置里，改动窗口不在这一轮。它实际改动的是本档案（新增了六个带出处的接线位与一条"策略≠数"的独立佐证），以及下一轮的两条具体动作：给准入/载荷归属建字段，把 provision 从扇出窗口里拆出来。

**一句话推荐**：最值得借的是 **bytedance/deer-flow**（MIT，83,050★，pushed 2026-09-27）——五个里唯一同时具备「N 个子 agent 在同一任务内并发」「准入控制器把请求值钳成执行容量」「编排器自己打 `started_at`」「准入失败与执行失败是两种终点」「身份由服务端铸造、不受 provider ID 复用影响」的仓库。**它与我们的根本不同**：它把并发上限当**策略**（缺省 3 running / 6 per run）来防失控，我们把 K 当**被测量**来找 N_max；而它一个墙钟加速数都不报。所以能借的是扇出点的记账接口，**一个常数都不能借**。次选 [google/adk-python](https://github.com/google/adk-python)（扇入哨兵计数＋每支背压的形状），第三 [BloopAI/vibe-kanban](https://github.com/BloopAI/vibe-kanban)（worktree provision 的逐路径锁与元数据冲突重试）。
