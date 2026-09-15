# 评测集（agent-parallelism）

遵循 Agent Skills 规范的评测结构。**未跑过真实 harness**——文件就绪，记分卡为空。

## 三根评测轴（缺一不可）

| 轴 | 文件 | 测什么 |
|---|---|---|
| **触发精度** | `eval_queries.json` | description 是否在该触发时触发、不该触发时休眠 |
| **输出质量（流程遵从）** | `evals.json` | 加载后是否按流程执行（给出 K 及其来源、落盘 contract 等）|
| **输出质量（禁止项遵从）** | `evals.json` | 是否规避明令禁止的做法（软隔离、融合、多数一致当验收）|

只测触发会漏掉静默的输出回归；只测输出会让触发失败不可见。

## 运行方法（每个用例跑两次）

每个用例在**独立上下文**中跑两遍：`with_skill` 与 `without_skill`（基线）。
单会话内连跑会引入跨用例污染，使评分失真。

```
agent-parallelism-workspace/
└── iteration-1/
    ├── eval-Q1/
    │   ├── with_skill/{outputs/, timing.json, grading.json}
    │   └── without_skill/{outputs/, timing.json, grading.json}
    └── benchmark.json
```

**grading.json**（每条断言必须附具体证据，不给"通融分"）：

```json
{
  "assertion_results": [
    { "text": "输出给出一个明确的数值并发度 K", "passed": true,
      "evidence": "原文：'K=4（拟合脚本输出 N_max=4.13）'" },
    { "text": "要求使用 git worktree 建立物理隔离", "passed": false,
      "evidence": "全文只提到按目录分配 owner，未出现 worktree" }
  ],
  "summary": { "passed": 1, "failed": 1, "total": 2, "pass_rate": 0.5 }
}
```

**benchmark.json** 记录每个配置的 `pass_rate` / `time_seconds` / `tokens` 与 `delta`——delta 说明这个 skill 花了多少（时间、token）、买到了什么（通过率）。

## 断言质量标准

- 必须**具体可观察**；"输出质量好" 不算断言
- 避免逐字比对——正确输出换个措辞不该判失败
- 机械可判的断言（文件存在、JSON 合法）优先用脚本判，别用 LLM 判官
- **同模型 LLM-as-judge 有位置偏差、自我偏好、文风偏好**，只能用于整体质量的盲测补充
- 迭代时回头审断言本身：两配置下**恒过**的断言没有区分度，应删；**恒挂**的断言多半写错了

## 收敛判据

结果达预期、人工反馈长期为空、或多轮无显著增益 → 收敛。

## 结构不变量自检（与评测互补）

上方三根轴测的是**行为**，需要真实 harness；`scripts/selfcheck.py` 测的是**结构**，随时可跑：

```bash
python3 scripts/selfcheck.py    # exit 0 才算过
```

覆盖 frontmatter 硬约束、跨文件数字一致、交叉引用可解析、contract/brief schema 一致、
speedup 定义、agentic-drift 检查项是否传导、评测文件结构、常驻记忆与 skill 是否同步等。

**两者不可互相替代**：结构自检全绿 ≠ 行为正确；行为评测通过也不能保证文件之间没有漂移。


## 静态预检结果（对应 SKILL.md v2.0.0，2026-09-15）

真实 harness 未跑。做法：把 `eval_queries.json` 的 25 条逐条去撞 `description` + `when_to_use`，标注误触发风险。**这是静态分析，不是实测触发率。**

### 撞出的两个真 bug（已修）

1. **`when_to_use` 与反例 N4 自相矛盾**：原 `when_to_use` 把「并行跑一下这些用例」列为**应触发**，而 N4「用 pytest-xdist 并行跑测试」标为**不应触发**——同一句话在 skill 内部被判成两个相反答案。已改例句并显式写出工具级并行的例外。
2. **Do-NOT-use 漏掉最危险的近失类**：原排除项只有"与并行无关的决策"，但 N3「把这段循环改成并行计算」**明明说了"并行"**，该条款拦不住。已显式加入「并行计算 / 多线程 / 加锁 / 用 xdist 跑测试」的排除。

### 对抗样本对（区分度最高）

**P8「加快执行速度，这个太慢了」（应触发）** vs **N8「这个接口响应时间太慢，优化一下」（不应触发）**
表层措辞几乎相同，区别仅在 P8 指**任务交付速度**、N8 指**程序运行时性能**。任何 description 改动都应优先回归这一对。

### 风险等级

| 等级 | 条目 |
|---|---|
| 高（与触发词直接重叠，靠语义消歧）| N3、N4、N8 |
| 中（部分词面重叠）| N5、N9 |
| 低（无明显词面重叠）| N1、N2、N6、N7、N10 |
| 边界（触发与否不算错）| B1~B5 |
