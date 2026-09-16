# Step 8 设计文档：去重体系（Dedup Optimizer）

> 对应 `docs/v2/PROGRESS.md` §5.10 与蓝图 Step 8。
> 本文档是 Step 8 的**单一设计真相源**，新会话接手 Step 9 前建议先读本文 + `step7-ai-reviewer.md`。

---

## 0. 一句话

**Step 8 = Dedup Optimizer**：消费 Step 7 `ReviewReport` 的 `duplication` findings（`auto_fixable=True`），执行确定性去重归档（canonicalize pairs + survivor priority + 双边状态检查），触发 `after_optimizer` 重评审（revision+1），形成 **生成→验证→评审→优化→复审** 自动质量闭环。

---

## 1. 定位与边界

### 1.1 在 V2 蓝图中的位置

```
Step 7 ReviewReport（duplication findings）
      ↓
Step 8 Dedup Optimizer（canonicalize → survivor → archive）
      ↓ 有归档？
      ├── 否 → DONE
      └── 是 → Step 7 AFTER_OPTIMIZER 重评审（revision+1）
              ↓
         验证 duplication 分数提升（闭环有效）
```

至此 V2 形成**第一个真正的 AI 测试质量闭环**：生成（Step 3/4/5）→ 验证（Step 5）→ 评审（Step 7）→ 优化（Step 8）→ 复审（Step 7）。

### 1.2 严格不做（Step 8 边界外）

| 项 | 归属 | 原因 |
|---|---|---|
| 重新计算语义重复 | Step 7 | Step 8 只消费 finding，不重新判重 |
| 修改 TestCase 内容（title/steps/expected/priority） | 未来 | Step 8 是 Dedup Optimizer，不是全能 Optimizer |
| 物理删除用例 | 永不做 | ARCHIVED 保留审计，可回滚 |
| 补充 missing_risk 遗漏用例 | 未来 | 需 LLM 语义生成，非机械修复 |
| 修复 consistency/executability finding | 未来 | 第一版仅去重 |
| 激活 TestCaseRevision 快照 | Step 9/10 | reserved.py 仍预留 |
| 改 V1 `core/generator.py` 的 deduplicate | 永不做 | V2 全新建 |

---

## 2. 5 条核心原则（用户冻结）

1. **只消费 Step 7 已确认的 duplication finding，不重新做重复判断**：职责分离，Step 7 判断"是不是重复"，Step 8 执行"怎么处理"。
2. **Duplicate pair 必须 canonicalize**（min_id, max_id → 唯一 pair）：防止 A↔B 两条 finding 导致两边都被归档（P0 坑）。
3. **Survivor 优先级**：HUMAN > OPTIMIZER > LLM > STRATEGY > VALIDATOR > MIGRATED → P0>P1>P2>P3 → created_at 越早 → display_id 越小。不覆盖人工意图，确定性可解释。
4. **ARCHIVED 用例保留审计，不物理删除**：可回滚，对齐 Repository 现状（无 delete_test_case）。
5. **Step 8 只改变重复用例状态，不改变 TestCase 内容**：Dedup Optimizer，内容优化留未来。

---

## 3. 架构数据流（用户冻结版）

```
Latest ReviewReport (repo.get_latest_review_report(run_id))
    ↓
Filter: auto_fixable=True + dimension=DUPLICATION
    ↓
Canonicalize duplicate pairs:
    for finding in findings:
        pair = (min(finding.target_id, finding.detail["counterpart_id"]),
                max(finding.target_id, finding.detail["counterpart_id"]))
        canonical_pairs.add(pair)  # set 去重，A↔B 只保留一条
    ↓
For each canonical pair (A_id, B_id):
    ├─ 拉 A = repo.get_test_case(A_id), B = repo.get_test_case(B_id)
    ├─ 双边状态检查:
    │   if A.status != REVIEWED or B.status != REVIEWED:
    │       if A.status == ARCHIVED or B.status == ARCHIVED:
    │           skip(reason="already_resolved")
    │       else:
    │           skip(reason="source_not_reviewed")
    │       continue
    ├─ Determine survivor:
    │   sort_key(tc) = (
    │       provenance_weight[tc.provenance],  # HUMAN=0, OPTIMIZER=1, LLM=2, STRATEGY=3, VALIDATOR=4, MIGRATED=5
    │       priority_weight[tc.priority],      # P0=0, P1=1, P2=2, P3=3
    │       tc.created_at,                     # 越早优先（ISO 字符串可直接比较）
    │       tc.display_id                      # 最终 tie-break（TC_001 < TC_002）
    │   )
    │   survivor, loser = min((A, B), key=sort_key), max((A, B), key=sort_key)
    ├─ Archive loser:
    │   loser.transition_to(TestCaseStatus.ARCHIVED)  # 状态机保证合法（REVIEWED→ARCHIVED 已放开）
    │   repo.update_test_case_status(loser.id, "archived")
    └─ Record OptimizerAction:
        finding_id, case_a_id, case_b_id, kept_case_id, archived_case_id
        reason = "exact_duplicate" | "semantic_duplicate"
        similarity = finding.detail.get("similarity")
    ↓
OptimizerResult(run_id, processed_findings, archived_cases, skipped_findings, actions[], skip_reasons)
    ↓
有实际归档（archived_cases > 0）？
    ├─ 否 → Run.status=DONE，返回 OptimizeResult（不触发重评审）
    └─ 是 → review_test_cases(client, run_id=run_id, trigger_type=AFTER_OPTIMIZER)
              ↓ revision 自动 +1（review_orchestrator 内部 get_latest_review_report+1）
              ↓ 重评审验证 duplication 分数提升
            Run.status=DONE，返回 OptimizeResult + ReviewResult
```

---

## 4. Schema / DDL 变更（schema_version 7 → 8）

### 4.1 状态机放开（`core/schemas/common.py`）

```python
# 当前（Step 7）
TestCaseStatus.REVIEWED: {TestCaseStatus.CONFIRMED, TestCaseStatus.EDITED},

# 改为（Step 8）
TestCaseStatus.REVIEWED: {TestCaseStatus.CONFIRMED, TestCaseStatus.EDITED, TestCaseStatus.ARCHIVED},
```

### 4.2 新增内存 dataclass（`core/v2/optimizer.py`，不持久化）

```python
@dataclass
class OptimizerAction:
    finding_id: str              # 触发此动作的 ReviewFinding.id
    case_a_id: str               # canonical pair 的 A（min_id）
    case_b_id: str               # canonical pair 的 B（max_id）
    kept_case_id: str            # 保留的用例 id
    archived_case_id: str        # 归档的用例 id
    action: str = "archive"
    reason: str = ""             # "exact_duplicate" | "semantic_duplicate"
    similarity: float | None = None

@dataclass
class OptimizerResult:
    run_id: str
    processed_findings: int = 0
    archived_cases: int = 0
    skipped_findings: int = 0
    actions: list[OptimizerAction] = field(default_factory=list)
    skip_reasons: dict[str, int] = field(default_factory=dict)
```

### 4.3 DDL（`core/v2/ddl.py`，SCHEMA_VERSION=8）

- Step 8 无 DDL 列变更（仅状态机代码层变更）
- 保留 `_migrate_v7_to_v8` 分支为未来扩展预留（如 optimizer_actions 表持久化）

### 4.4 不建 optimizer_actions 表

第一版 `ReviewFinding` + `OptimizerResult`（内存）+ `TestCase.status` 变更已足够审计。未来需要长期记录每次 optimizer 执行动作时再持久化。

---

## 5. 交付文件清单

### 5.1 新增（core/v2/）

| 文件 | 职责 |
|---|---|
| `optimizer.py` | 去重逻辑：canonicalize pairs, 双边状态检查, determine survivor, archive loser, record actions |
| `optimizer_orchestrator.py` | 编排：拉 latest report → 执行 optimizer → 有归档则触发 after_optimizer 重评审 → Run 状态机 |

### 5.2 升级

- `core/schemas/common.py`：状态机放开 REVIEWED → ARCHIVED
- `core/v2/ddl.py`：SCHEMA_VERSION 7→8 + `_migrate_v7_to_v8` 分支

### 5.3 测试（tests/，全 mock + 集成）

| 文件 | 例数 | 覆盖 |
|---|---|---|
| `test_optimizer.py` | 35 | canonicalize, 双边状态检查, survivor 优先级, 去重归档, 幂等 |
| `test_optimizer_orchestrator.py` | 7 | 编排：拉 report, 触发重评审条件, Run 状态机 |
| `test_step8_acceptance.py` | 25 | 14 门槛逐条（集成测试，使用 v2_db fixture） |

---

## 6. 14 条验收门槛（全部 PASSED）

| # | 门槛 |
|---|---|
| 1 | 只消费 duplication finding（不碰 coverage/consistency/executability/accuracy/missing_risk） |
| 2 | Canonical pair 去重（A↔B 两条 finding 只处理一次，不会两边都归档） |
| 3 | 双边状态检查（任一方非 REVIEWED → skip） |
| 4 | already_resolved skip（任一方 ARCHIVED → skip，记录原因） |
| 5 | Survivor 优先级正确（HUMAN>LLM, P0>P1, created_at 早者优先, display_id 小者优先） |
| 6 | 归档不物理删除（status=ARCHIVED，DB 行保留，可审计） |
| 7 | OptimizerAction 记录完整（finding_id, case_a_id, case_b_id, kept_case_id, archived_case_id, reason, similarity） |
| 8 | 幂等：多次运行结果一致（已归档的不再动，skip_reasons 累计） |
| 9 | 有归档才触发重评审（archived_cases=0 → 不触发，直接 DONE） |
| 10 | 重评审 revision+1，trigger_type=AFTER_OPTIMIZER |
| 11 | 去重后 duplication 分数提升（验证闭环有效） |
| 12 | schema_version=8，状态机 REVIEWED→ARCHIVED 生效 |
| 13 | V1 零回归（core/generator.py、web/、data.db 不动） |
| 14 | 不修改 TestCase 内容（只改 status，title/steps/expected/priority 不变） |

---

## 7. 关键决策记录（ADR）

- **D1 只消费 finding 不重新判重**：Step 7 负责"是不是重复"，Step 8 负责"怎么处理"。避免两个模块各算一遍结果不一致。
- **D2 Canonicalize pairs 是 P0 坑防护**：A↔B 两条 finding 如果不 canonicalize，会导致两边都被归档。min_id/max_id 确保唯一 pair。
- **D3 Survivor 优先级 HUMAN 最前**：不覆盖人工意图。HUMAN 修改过的用例永远优先保留，即使 priority 更低。
- **D4 created_at 越早优先**：生成序早的用例更稳定（文档明确，防止实现人员误解为"最新生成的优先"）。
- **D5 归档不物理删除**：ARCHIVED 保留审计痕迹，可回滚。对齐 Repository 现状（无 delete_test_case）。
- **D6 有归档才触发重评审**：避免无意义重评审（全部 skip 时直接 DONE）。
- **D7 OptimizerResult 内存不持久化**：第一版 ReviewFinding + OptimizerResult + TestCase status change 已足够审计。未来需要长期记录再建表。
- **D8 只改 status 不改内容**：Dedup Optimizer，不是全能 Optimizer。内容优化（改 title/steps/expected）留未来。

---

## 8. 诚实边界（第一版不做）

1. **不建 optimizer_actions 表**：内存 OptimizerResult 足够审计，未来需要长期记录再持久化。
2. **不引入两级语义阈值**（0.85≤sim<0.90 仅标记，sim≥0.90 自动归档）：第一版统一 0.85 自动归档，留 Step 12 Benchmark 调。
3. **不做内容优化**（改 title/steps/expected/priority）：Dedup Optimizer 只去重，内容优化留未来。
4. **不激活 TestCaseRevision 快照**：reserved.py 仍预留，Step 9/10 再激活。
5. **不做"反悔"机制**（ARCHIVED → REVIEWED 恢复）：留 Step 9 人工确认闭环。
6. **不处理跨 run 重复**：只在同一 run 内去重（Step 7 duplication finding 也是 run 内检测）。

---

## 9. Step 8 完成后的系统变化

**第一个真正的 AI 测试质量闭环**：

```
Step 5 生成 TestCase
    ↓
Step 7 发现重复（duplication findings）
    ↓
Step 8 自动归档重复（Dedup Optimizer）
    ↓
Step 7 AFTER_OPTIMIZER 重评审（revision+1）
    ↓
验证 duplication 分数提升（闭环有效）
```

**保持**：
- ✅ 不删除数据（ARCHIVED 保留审计）
- ✅ 不修改有效 TestCase 内容（只改 status）
- ✅ 不重新判重（消费 Step 7 finding）
- ✅ 不碰 V1（core/generator.py、web/、data.db 不动）
- ✅ 可重复执行（幂等，双边状态检查）
- ✅ 可审计（OptimizerAction 记录完整）
- ✅ 不自动无限循环（有归档才重评审，重评审后不再 optimize）

---

## 10. 验证命令（PowerShell）

```powershell
# 全量（期望 819 passed = Step 7 后 752 + Step 8 新增 67）
.\.venv\Scripts\python.exe -m pytest -q

# Step 8 专项（期望 67 passed = 35+7+25）
.\.venv\Scripts\python.exe -m pytest tests/test_optimizer.py tests/test_optimizer_orchestrator.py tests/test_step8_acceptance.py -v

# Lint
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

---

## 11. 下一步 = Step 9「人工编辑/确认闭环」

见 `docs/v2/PROGRESS.md` §9。开工前需与用户讨论确认：
- TestCase 状态机 EDITED → RE_REVIEW_REQUIRED → REVIEWED 闭环
- 人工编辑后是否触发重评审（trigger_type=AFTER_HUMAN_EDIT）
- ARCHIVED 用例是否允许人工恢复（"反悔"机制）
- TestCaseRevision 快照激活时机
- 前端交互设计（Step 13）
