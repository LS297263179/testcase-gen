# Step 7 设计文档：AI Reviewer 6 维结构化评审

> 对应 `docs/v2/PROGRESS.md` §5.9 与蓝图 Step 7。
> 本文档是 Step 7 的**单一设计真相源**，新会话接手 Step 8 前建议先读本文 + `step6-traceability-change-impact.md`。

---

## 0. 一句话

**Step 7 = AI Reviewer 6 维结构化评审**：消费 Step 5 的 `TestCase[]`（status=VALIDATED），经 **Hard Review Engine**（代码算 coverage 双指标 / duplication 两级 / consistency / executability 结构层）与 **LLM Soft Review Engine**（accuracy / missing_risk / executability 语义层，soft score 必带 reason + 证据锚定 findings）合成结构化 `ReviewReport`（6 维 `ReviewScores` + 明细 + findings），TestCase 转 `REVIEWED`（仅代表评审完成，非质量合格），auto_fixable finding **只标记不修复**（Optimizer 是 Step 8）。

---

## 1. 定位与边界

### 1.1 在 V2 蓝图中的位置

```
Step 5 TestCase[]（VALIDATED）─┐
Step 6 追溯/影响（可选消费）  ─┤→ Step 7 AI Reviewer（Hard + Soft）→ ReviewReport
Step 4 obligation_coverage   ─┘         ↓ TestCase VALIDATED→REVIEWED
                                        ↓ auto_fixable findings 仅标记
                                   Step 8 Optimizer/Dedup → Step 9 人工确认
```

至此 V2 形成完整闭环：**生成（Step 3/4/5）→ 验证（Step 5 Validator）→ 评审（Step 7）**。Step 8 才处理"评审发现的问题如何自动优化/去重/补全"。

### 1.2 严格不做（Step 7 边界外，用户强调"一定要忍住"）

| 项 | 归属 | 原因 |
|---|---|---|
| merge/delete 重复用例、修复 finding、补充遗漏用例 | Step 8 Optimizer | Step 7 只发现问题、只标记 auto_fixable |
| 改 TestCase 内容为"合格" | Step 8/9 | REVIEWED ≠ 合格 |
| 人工编辑/确认闭环 | Step 9 | — |
| 新增 TestCase 状态（review_result PASS/NEEDS_OPTIMIZATION/CRITICAL） | 未来 | 用户明确现在不加状态 |
| 改 V1 `core/reviewer.py`（自由文本评审） | 永不做 | V2 全新建 |

---

## 2. 9 条核心原则（用户冻结）

1. **混合分工**：确定性维度代码硬算，语义维度 LLM 软判，各尽其职不互相替代。
2. **executability 双子指标加权，不简单平均**：`structural(Code)×0.4 + semantic(LLM)×0.6`。steps 非空只证"形式上有步骤"，不证"真能执行"。
3. **coverage 保持双指标，不合成模糊分**：Report 保留 `strategy_obligation_coverage` + `requirement_item_coverage` + `uncovered_item_ids`。
4. **duplication 分两级**：`EXACT`（content_hash 精确）/ `SEMANTIC`（相似度 + similarity）；只报告 + auto_fixable，绝不 merge/delete。
5. **REVIEWED ≠ 质量合格**：仅代表"评审已完成"；score=62 + findings=4 的用例照样 REVIEWED。
6. **评分可解释**：所有 LLM soft score 必带 `score + reason + findings`。
7. **LLM Review 必须引用证据**：finding 指向真实 TestCase/RequirementItem（ReferentialValidator 校验），missing_risk 引用具体 item/rule。
8. **只发现问题，不做 Optimizer**：发现 → 记 finding → 标 auto_fixable → 停止。
9. **V1 运行时零修改**。

---

## 3. 架构数据流（用户冻结）

```
TestCase[]（某 run，status=VALIDATED）
      ↓ Validator（确定性检查）
      ├── Hard Review Engine（代码，review_hard.py）   ├── LLM Soft Review Engine（review_soft.py）
      │   coverage 双指标 + uncovered findings          │   accuracy（score+reason+findings）
      │   duplication 两级(exact/semantic)+auto_fixable │   missing_risk（引用 item/rule 证据）
      │   consistency（display_id/module/title 规范）    │   executability 语义层（score+reason）
      │   executability 结构层（steps/action/expected）  │
      └──────────────────┬─────────────────────────────┘
                         ↓ 合成 ReviewScores(6维) + coverage_detail + executability_detail
                           + dimension_reasons + findings(validator+llm)
                  ReviewReport(revision, trigger=initial, review_target=TestCase[])
                         ↓ save_review_report（已就绪：findings 拆表 + ReferentialValidator）
                  TestCase VALIDATED → REVIEWED（全部转；REVIEWED≠合格）
                         ↓ auto_fixable findings 仅标记
                      Step 8（Optimizer/Dedup）
```

---

## 4. Schema / DDL 变更（schema_version 6 → 7）

### 4.1 新增枚举（common.py）
```python
class DuplicateLevel(StrEnum): EXACT="exact"; SEMANTIC="semantic"
```

### 4.2 新增子模型 + 扩展（review.py）
```python
class CoverageDetail(BaseModel):
    strategy_obligation_coverage: float   # [0,1] Step 4 硬指标
    requirement_item_coverage: float      # [0,1] item 覆盖率
    uncovered_item_ids: list[Ulid] = []

class ExecutabilityDetail(BaseModel):
    structural_score: float               # [0,100] Code
    semantic_score: float                 # [0,100] LLM
    structural_weight: float = 0.4
    semantic_weight: float = 0.6

# ReviewFinding 扩展
detail: dict | None = None   # duplication:{duplicate_level,similarity,counterpart_id} / missing_risk:{rule_name,evidence} / consistency:{violation}

# ReviewReport 扩展（ReviewScores 6 维不变）
review_target_type: TargetType = TargetType.TESTCASE   # 预留：当前 TESTCASE，未来可 TESTPOINT
review_target_ids: list[Ulid] = []
coverage_detail: CoverageDetail | None = None
executability_detail: ExecutabilityDetail | None = None
dimension_reasons: dict[str, str] = {}                 # 各维度评分理由（软维度来自 LLM）
```

**ReviewScores 保持 6 维不变**（Step 1 冻结）：`executability` 存加权最终分；`coverage` 存 `requirement_item_coverage×100`（strategy 硬指标在 coverage_detail 佐证，避免被其 =1.0 稀释）；`overall()` 沿用 6 维均值。

### 4.3 DDL（ddl.py，SCHEMA_VERSION=7）
- `review_reports` 加 5 列：`review_target_type` / `review_target_ids_json` / `coverage_detail_json` / `executability_detail_json` / `dimension_reasons_json`
- `review_findings` 加 `detail_json`
- `_migrate_v6_to_v7`：ALTER TABLE 补列（新列可空/有默认，旧行无需回填业务值）

### 4.4 repository.py
- `save_review_report`：upsert 加 5 列 + 序列化；findings 插入加 `detail_json`（ReferentialValidator 校验不变）
- `get/list_review_report(s)`：反序列化新字段
- 新增 `get_latest_review_report(run_id)`（max revision）

---

## 5. 6 维评分机制（硬软分工）

| 维度 | 谁算 | 评分 | findings |
|---|---|---|---|
| coverage | 代码 | `requirement_item_coverage×100`；detail 存双指标+uncovered | 未覆盖 item → COVERAGE finding(target=REQUIREMENT_ITEM, validator) |
| duplication | 代码 | `100 - 涉及重复用例占比×100` | exact/semantic → DUPLICATION finding(target=TESTCASE, auto_fixable=true, detail) |
| consistency | 代码 | `100 - 违规用例占比×100` | display_id 格式/唯一/module/title → CONSISTENCY finding(validator, detail.violation) |
| executability | 代码+LLM | `structural×0.4 + semantic×0.6` | 结构缺陷(validator, layer=structural) + 语义不可执行(llm) |
| accuracy | LLM | LLM 打分+reason | 步骤/预期不合理 → ACCURACY finding(llm) |
| missing_risk | LLM | LLM 打分+reason | 遗漏场景 → MISSING_RISK finding(target=REQUIREMENT_ITEM, llm, detail.rule_name) |

---

## 6. Hard Review Engine（review_hard.py，纯代码确定性）

- **coverage**：复用 `tp_validator.build_coverage_report(test_points, items)` 得 requirement_item_coverage + uncovered_item_ids；strategy 侧传入 `obligation_coverage_ratio(run_id)`。双指标写入 CoverageDetail。
- **duplication**：两两比对；`content_hash` 相同 → EXACT（similarity=1.0）；否则归一化 `title+steps_text` 的 `difflib.SequenceMatcher.ratio() ≥ 0.85` → SEMANTIC。每对产 1 条 finding（target=后者，detail.counterpart_id=前者），auto_fixable=true。
- **consistency**：display_id 匹配 `^TC_\d{3,}$` + run 内唯一 + module/title 非空。
- **executability 结构层**：steps 非空 + 每步 action 非空 + expected 非空。
- **确定性**：同一输入多次运行分数/findings 完全一致（门槛 12 稳定性基础）。

---

## 7. LLM Soft Review Engine（review_soft.py）

### 7.1 结构化输出 + 证据锚定
Prompt（`test-case-reviewer-v1`）只让 LLM 评 3 个软维度，每维输出 `{dimension, score, reason, findings[]}`；finding 含 `target_type/target_ref/severity/issue/suggestion/detail`。user prompt 提供每条 TestCase 的 id/display_id + 每个 RequirementItem 的 id/statement/rules(name)/source_ref，使 LLM 能引用真实 ULID 与 rule_name。

### 7.2 代码校验（LLM 不能自证）
- `extract_json` 解析（复用 Step 2）
- dimension 白名单（accuracy/missing_risk/executability_semantic）；LLM 越界评硬维度 → 忽略 + issue
- score clamp [0,100]；非法 → 中性默认
- **target_ref 解析 + 校验**：支持 ULID 或 display_id（映射回 id）；经 `TargetResolver.exists` 权威校验；非法/编造 → 丢弃 finding + 记 issue（证据锚定失败）
- 软维度 finding `provenance=LLM`、`auto_fixable=False`（多需语义修复/重生成）
- **降级**：LLM 调用/解析失败或缺维度 → 用中性默认分 `DEFAULT_SOFT_SCORE_ON_FAILURE=50` + 记 issue（不假装高质量）

---

## 8. 用户核心设计三处（务必守住）

### 8.1 executability 加权而非平均
`executability = structural×0.4 + semantic×0.6`。反例：structural=100、semantic=75 → 85（非 (100+75)/2=87.5）。ExecutabilityDetail 保留两个子分供审计与 Benchmark 调权。

### 8.2 coverage 双指标不合成
CoverageDetail 同时保留 strategy_obligation_coverage（Step 4 结构性硬指标，通常 1.0）与 requirement_item_coverage（需求被测试覆盖程度）+ uncovered_item_ids。Reviewer 能定位"12% item 未被任何测试点覆盖"，而非只看一个模糊的 coverage=94。

### 8.3 duplication 两级 + REVIEWED≠合格
- EXACT（content_hash）可直接删、SEMANTIC（相似度）需人工/Step 8 判断 → detail.duplicate_level 供 Step 8 区分。
- REVIEWED 仅"评审完成"：低分（如 62）+ 多 findings 的用例照样 VALIDATED→REVIEWED；不新增状态；review_result（PASS/NEEDS_OPTIMIZATION/CRITICAL）留未来。

---

## 9. 编排（review_orchestrator.py）

`review_test_cases(client, *, run_id, revision=None, trigger_type=INITIAL) -> ReviewResult`：
1. 拉 Run，status→REVIEWING，追加 `GenerationConfig.reviewer_version`（审计）
2. 拉该 run 的 TestCase[]，仅纳入 status=VALIDATED（空则返回带 issue 的结果，不产报告）
3. 拉 items + test_points + `obligation_coverage_ratio`
4. Hard Review Engine → 硬维度分 + coverage_detail + validator findings
5. Soft Review Engine（LLM）→ 软维度分 + reason + llm findings
6. 合成 ReviewScores（executability 加权）+ executability_detail + dimension_reasons + findings
7. revision = get_latest_review_report+1（首评=1）→ 建 ReviewReport（review_target=TestCase[]）→ save_review_report
8. TestCase VALIDATED→REVIEWED（`transition_to` 保证状态机合法；全部转，含低分）
9. Run.status→DONE；返回 `ReviewResult{report, reviewed_count, issues}`
- **预留**：消费 Step 6 ChangeImpactReport 对受影响资产加权复审——首版不实现（留接口注释）。

---

## 10. 交付文件清单

### 10.1 新增（core/v2/）
| 文件 | 职责 |
|---|---|
| `review_prompts.py` | 结构化评审 Prompt（test-case-reviewer-v1）+ 证据锚点 user 模板 |
| `review_hard.py` | Hard Review Engine（coverage/duplication/consistency/executability 结构层 + validator findings） |
| `review_soft.py` | LLM Soft Review Engine（软维度解析 + reason + 证据校验 + 兜底） |
| `review_orchestrator.py` | review_test_cases 编排 + ReviewReport 组装 + TestCase→REVIEWED + Run 状态机 |

### 10.2 升级
common.py（DuplicateLevel）、review.py（CoverageDetail/ExecutabilityDetail + ReviewFinding.detail + ReviewReport 5 字段）、__init__.py、ddl.py（v7）、repository.py（review 序列化 + get_latest_review_report）

### 10.3 测试（tests/，全 mock）
| 文件 | 例数 | 覆盖 |
|---|---|---|
| `test_review_hard.py` | 17 | coverage 双指标、duplication 两级、consistency、executability 结构、确定性稳定 |
| `test_review_soft.py` | 19 | 解析、reason、证据引用、非法 target 兜底、clamp、降级 |
| `test_review_orchestrator.py` | 19 | 端到端、持久化、状态转移、revision、加权、只读 |
| `test_step7_acceptance.py` | 15 | 14 门槛逐条 |
| 更新 test_schemas/test_v2_roundtrip | +3/扩展 | 新枚举/子模型 + ReviewReport 新字段往返 |

---

## 11. 14 条验收门槛（全部 PASSED）

| # | 门槛 |
|---|---|
| 1 | 生成 ReviewReport 并持久化（revision=1, trigger=initial），6 维 ReviewScores[0,100]+overall |
| 2 | coverage 双指标保留（strategy + item + uncovered_item_ids，不合成模糊分） |
| 3 | executability = structural×0.4 + semantic×0.6（子指标保留，非简单平均） |
| 4 | duplication 两级 EXACT/SEMANTIC（semantic 带 similarity），auto_fixable=true，不删不改用例 |
| 5 | Validator findings provenance=validator；LLM findings provenance=llm |
| 6 | LLM soft score 必带 reason（dimension_reasons 非空，可解释） |
| 7 | missing_risk finding 引用真实 RequirementItem（ReferentialValidator 通过）+ detail.rule_name |
| 8 | finding.target 多态经校验；非法 target 丢弃并记 issue |
| 9 | TestCase VALIDATED→REVIEWED 全部转；REVIEWED≠合格（低分+多 findings 也转） |
| 10 | review_target_type=TESTCASE + review_target_ids=被评审 TestCase[] |
| 11 | Step 7 只标记不修复：auto_fixable finding 不改动用例；无重生成；无 Optimizer |
| 12 | 稳定性：同一 Run Review×3，硬指标(coverage/duplication/consistency/structural)完全一致；LLM 软分允许波动 |
| 13 | schema_version=7；review_reports/review_findings 新列 + 迁移分支生效 |
| 14 | V1 零回归（core/reviewer.py、web/、data.db 不动） |

---

## 12. 关键决策记录（ADR）

- **D1 executability 加权而非平均**：结构合规≠可执行；structural 证形式、semantic 证实质，0.4/0.6 突出语义（可 Benchmark 调）。
- **D2 coverage 双指标**：strategy=100 是 Step 4 结构性保证，item coverage 才反映需求被覆盖程度；合成一个数会掩盖 item 缺口。
- **D3 ReviewScores 不改 6 维**：Step 1 冻结的 6 维是稳定评分面；明细挂 ReviewReport 扩展字段，最小侵入。
- **D4 duplication 两级 + 只报告**：exact 可直接删、semantic 需判断；Step 7 报告+标记，Step 8 决策。
- **D5 REVIEWED≠合格**：分离"评审完成"与"评审通过"，不新增状态，低分照样 REVIEWED。
- **D6 LLM 评分强制 reason+证据**：防"漂亮分数无依据"；接 Step 2 source_ref，让 Review 工程化可追溯。
- **D7 不塞 Optimizer**：Step 7 发现、Step 8 处理；coverage gap/accuracy/missing_risk 多不可机械修复。

---

## 13. 诚实边界

1. LLM 软分波动：mock 只验证"硬指标确定性稳定 + LLM 输出被正确解析/校验/证据锚定"，不验证真实打分质量（需真实 API 抽查）。
2. executability 0.4/0.6、overall 均值权重为经验值，留 Step 12 Benchmark 调整。
3. review_target 预留字段当前仅 TESTCASE；testpoint 评审为未来扩展。
4. review_result（PASS/NEEDS_OPTIMIZATION/CRITICAL）不实现（用户明确现在不加状态）。
5. 消费 ChangeImpactReport 的"变更后加权复审"首版不做，仅留接口注释。

---

## 14. 验证命令（PowerShell）

```powershell
# 全量（期望 752 passed = Step 6 后 679 + Step 7 新增 73）
.\.venv\Scripts\python.exe -m pytest -q
# Step 7 专项（期望 70 passed = 17+19+19+15）
.\.venv\Scripts\python.exe -m pytest tests/test_review_hard.py tests/test_review_soft.py tests/test_review_orchestrator.py tests/test_step7_acceptance.py -v
.\.venv\Scripts\python.exe -m ruff check . ; .\.venv\Scripts\python.exe -m ruff format --check .
```

---

## 15. 下一步 = Step 8「去重体系（精确 + 语义）+ Optimizer」

见 `docs/v2/PROGRESS.md` §9。开工前需与用户讨论确认：
- 去重策略（精确 content_hash 直接删 vs 语义相似度保留哪条）
- Optimizer 范围（仅去重 vs 修复 auto_fixable finding + 补遗漏用例）
- 去重/优化后的状态与审计（被删用例 ARCHIVED？优化后触发 after_optimizer 重评审 revision+1）
- 幂等与可回滚（TestCaseRevision 预留）
- 与 Step 9 人工确认的边界
