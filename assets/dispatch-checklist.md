# 派发前检查清单

**逐项勾选，任一项未过 → 降到 K=1。**

- [ ] 已测单 agent 基线并记录成功率，对照 45%
- [ ] 可分解性三问全部通过
- [ ] K 由脚本或参数表得出，**不是估计的**
- [ ] K 未超过限流上限（见 SKILL.md「限流是另一回事」）
- [ ] `contract.md` 已按 `contract-template.md` 落盘到项目目录
- [ ] 并发写入已用 `git worktree` 建立**物理**隔离（仅有 owner 表不算）
- [ ] 验收命令已确定且可机器判定（`pytest -x` / `tsc --noEmit` 等）
- [ ] 合并为测试门控：测试通过才 merge，main 始终可运行
- [ ] 子 agent 返回 schema 已固定
- [ ] 已安排主 agent 或显式 verifier 做中心化核对
- [ ] 每个子 agent 的 brief 按 `subagent-brief-template.md` 写（目标 / 背景 / 硬边界 / 命令级验收 / JSON 返回）

## 派发后

- [ ] 记录 `(K, 串行耗时, 并行耗时, 合并耗时, 返工耗时)` 到 `parallel-log.csv`
- [ ] 子 agent 输出**同题择优、异题拼装**，不做融合
- [ ] 不用「多数一致」当验收
- [ ] **查重复实现**（agentic drift）：同一概念是否被多个分片各实现了一遍？测试通过不等于没有这个问题
