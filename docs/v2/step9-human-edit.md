# Step 9 设计文档：人工编辑/确认闭环

> 对应 `docs/v2/PROGRESS.md` §5.11 与蓝图 Step 9。
> 本文档是 Step 9 的**单一设计真相源**，新会话接手 Step 10 前建议先读本文 + `step8-dedup-optimizer.md`。

---

## 0. 一句话

**Step 9 = Human Editor**：实现 TestCase 人工编辑闭环，激活 `TestCaseRevision` 快照（修改前完整内容 + changed_fields），引入乐观锁并发控制，保护 identity fingerprint 不变、content_hash 重算，编辑后必须过 Validator，用户主动触发 `AFTER_HUMAN_EDIT` 重评审。形成 **AI + 代码 + 人** 三者协同的完整测试设计流程。

---

## 1. 定位与边界

### 1.1 在 V2 蓝图中的位置

```
Step 8 Dedup Optimizer（自动归档重复）
      ↓
Step 9 Human Editor（人工编辑 + Revision 快照 + Validator）
      ↓ 用户主动点击 [重新评审]
Step 7 Review Engine (trigger=AFTER_HUMAN_EDIT, review_revision+1)
      ↓
REVIEWED（闭环完成）
```

至此 V2 形成 **AI + 代码 + 人 三者协同的完整测试设计流程**：
LLM 生成 → 代码验证 → AI Review → 自动优化 → 人工修改 → 再次验证 → 再次 Review。

### 1.2 严格不做（Step 9 边界外）

| 项 | 归属 | 原因 |
|---|---|---|
| 前端 UI | Step 13 | Step 9 仅提供 API/Python 接口 |
| batch queue / parallel review / retry | 未来 | 第一版批量接口仅 list 参数，内部统一 Review |
| Re-link TestCase（修改 test_point_ids） | 未来 | 追溯链由系统维护，需单独设计申请→记录→校验→重审流程 |
| auto_review_on_edit 配置项 | 未来 | 太早，增加复杂度，观察真实使用行为后再考虑 |
| Revision 差异对比 UI | Step 13 | 仅保存 snapshot + changed_fields，可视化留未来 |
| 多用户协同编辑锁（悲观锁） | 未来 | 仅乐观锁（updated_at），不做编辑锁 |

---

## 2. 9 条核心原则（用户冻结）

1. **编辑范围白名单**：title/steps/expected/precondition/priority/module/remark 可改；test_point_ids + 系统字段（id/run_id/version_id/fingerprint/created_at/schema_version）不可改。
2. **不自动重评审**：保存=标记 RE_REVIEW_REQUIRED；用户主动点击才触发 Review(AFTER_HUMAN_EDIT)。
3. **激活 TestCaseRevision**：编辑前创建快照（revision_no+1），保存修改前完整 TestCase + changed_fields。
4. **revision_no 语义区分**：`test_case_revision_no`（内容版本）vs `review_revision`（评审版本），代码不混叫。
5. **fingerprint/content_hash 区分**：identity fingerprint 不变（同一 TestCase），content_hash 重算（内容变了）。
6. **编辑后必须过 Validator**：FAIL→VALIDATION_FAILED，PASS→RE_REVIEW_REQUIRED。
7. **VALIDATION_FAILED 可恢复**：用户重新编辑 → EDITED → Validator（不是死状态）。
8. **乐观锁并发控制**：保存时 WHERE updated_at=?，冲突→整体事务回滚（无错误 Revision，无半截 TestCase）。
9. **provenance 语义**：Revision.provenance="这一版谁改的"；TestCase.provenance="当前生效版本来源"（编辑后=HUMAN）。

---

## 3. 架构数据流（用户冻结版）

```
TestCase (status=REVIEWED, revision_no=N)
    ↓
Edit API（人工修改 title/steps/expected/...）
    ↓
白名单过滤（拒绝 test_point_ids + 系统字段修改）
    ↓
状态检查（REVIEWED / VALIDATION_FAILED / RE_REVIEW_REQUIRED 可编辑）
    ↓
乐观锁检查（WHERE updated_at=?，冲突→整体回滚）
    ↓
Create Pre-Edit Snapshot（TestCaseRevision revision_no=N+1）
    ├─ snapshot = 修改前完整 TestCase
    ├─ changed_fields = [被修改的字段名]
    ├─ provenance = HUMAN
    └─ changed_by = "user", change_source = "human_edit"
    ↓
Current TestCase Update
    ├─ provenance = HUMAN（当前生效版本来源）
    ├─ fingerprint = unchanged（identity 不变）
    ├─ content_hash = recompute（内容变了）
    └─ status = EDITED
    ↓
Validator（结构完整性：title/steps/action/expected/module 非空）
    ├── FAIL → status=VALIDATION_FAILED（可恢复：用户重新编辑→EDITED）
    └── PASS → status=RE_REVIEW_REQUIRED
    ↓
持久化（乐观锁 UPDATE + Revision INSERT，冲突则整体回滚）
    ↓
用户点击 [重新评审]
    ↓
Review Engine (trigger=AFTER_HUMAN_EDIT, review_revision=latest+1)
    ↓
ReviewReport
    ↓
status=REVIEWED
```

---

## 4. Schema / DDL 变更（schema_version 8→9）

### 4.1 状态机放开（`core/schemas/common.py`）

```python
# 当前（Step 8）
TestCaseStatus.VALIDATION_FAILED: {TestCaseStatus.GENERATED},

# 改为（Step 9）
TestCaseStatus.VALIDATION_FAILED: {TestCaseStatus.GENERATED, TestCaseStatus.EDITED},  # 可人工重新编辑修复
```

### 4.2 激活 TestCaseRevision（`core/schemas/reserved.py` → 建表）

```python
class TestCaseRevision(EntityBase):
    test_case_id: Ulid
    revision_no: int = Field(ge=1)  # 内容版本（test_case_revision_no，非 review_revision）
    snapshot: dict = Field(default_factory=dict)  # 修改前完整 TestCase 快照
    changed_fields: list[str] = Field(default_factory=list)  # 哪些字段被修改（Step 10 数据源）
    provenance: Provenance = Provenance.LLM  # 这一版本是谁产生/修改的
    changed_by: str = "system"  # user / system / optimizer
    change_source: str = ""  # human_edit / llm_generation / optimizer_archive
```

### 4.3 DDL 新增表（`core/v2/ddl.py`，SCHEMA_VERSION=9）

```sql
CREATE TABLE IF NOT EXISTS test_case_revisions (
    id                  TEXT PRIMARY KEY,
    test_case_id        TEXT NOT NULL REFERENCES test_cases(id) ON DELETE CASCADE,
    revision_no         INTEGER NOT NULL,
    snapshot_json       TEXT NOT NULL,
    changed_fields_json TEXT NOT NULL DEFAULT '[]',
    provenance          TEXT NOT NULL DEFAULT 'llm',
    changed_by          TEXT NOT NULL DEFAULT 'system',
    change_source       TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    schema_version      INTEGER NOT NULL DEFAULT 2,
    UNIQUE (test_case_id, revision_no)
);
CREATE INDEX IF NOT EXISTS idx_revisions_case ON test_case_revisions(test_case_id);
```

### 4.4 乐观锁（复用现有 updated_at，无需新增列）

保存时：`UPDATE test_cases SET ... WHERE id=? AND updated_at=?`，影响行数=0→抛 `ConcurrentModificationError`，整体事务回滚。

---

## 5. 交付文件清单

### 5.1 新增（core/v2/）

| 文件 | 职责 |
|---|---|
| `human_editor.py` | 编辑逻辑：白名单过滤 + Revision 快照 + changed_fields + Validator + fingerprint/content_hash + 乐观锁 + provenance |
| `human_editor_orchestrator.py` | 编排：edit_case（单条）+ re_review_test_cases（批量接口预留，第一版内部统一 Review） |

### 5.2 升级

- `core/schemas/common.py`：状态机放开 VALIDATION_FAILED → EDITED
- `core/schemas/reserved.py`：TestCaseRevision 扩展（revision→revision_no + changed_by + change_source）
- `core/v2/ddl.py`：SCHEMA_VERSION=9 + test_case_revisions 表 + `_migrate_v8_to_v9` 分支
- `core/v2/repository.py`：save/get TestCaseRevision + get_latest_revision_no + update_test_case_with_lock（乐观锁）+ ConcurrentModificationError
- `core/v2/review_orchestrator.py`：支持 RE_REVIEW_REQUIRED 状态用例的重评审

### 5.3 测试（tests/）

| 文件 | 例数 | 覆盖 |
|---|---|---|
| `test_human_editor.py` | 30 | 白名单, Revision 快照, changed_fields, Validator, fingerprint, content_hash, 乐观锁, provenance |
| `test_human_editor_orchestrator.py` | 7 | 编排：edit_case 包装, re_review 批量接口, AFTER_HUMAN_EDIT trigger |
| `test_step9_acceptance.py` | 22 | 19 门槛逐条（集成测试，使用 v2_db fixture） |

---

## 6. 19 条验收门槛（全部 PASSED）

| # | 门槛 |
|---|---|
| 1 | title/steps/expected/precondition/priority/module/remark 可人工修改 |
| 2 | test_point_ids 不可通过普通编辑接口修改（拒绝并记录） |
| 3 | 系统字段（id/run_id/version_id/fingerprint/created_at/schema_version）不可修改 |
| 4 | 编辑前自动生成 Revision Snapshot（revision_no+1，保存修改前完整内容） |
| 5 | Revision 记录 changed_fields（哪些字段被修改） |
| 6 | 人工编辑后 TestCase.provenance = HUMAN |
| 7 | Revision.provenance = HUMAN（这一版本是谁改的） |
| 8 | identity fingerprint 不变化（还是同一 TestCase） |
| 9 | content_hash 重新计算且发生变化（内容变了） |
| 10 | 编辑后必须重新通过 Validator |
| 11 | Validator FAIL → VALIDATION_FAILED（不进入 RE_REVIEW_REQUIRED） |
| 12 | VALIDATION_FAILED → EDITED 可恢复（不是死状态，用户重新编辑修复） |
| 13 | REVIEWED → EDITED → RE_REVIEW_REQUIRED → REVIEWED 全流程成立 |
| 14 | 并发验收：旧版本保存时拒绝覆盖新版本（乐观锁 updated_at） |
| 15 | 乐观锁冲突时整个事务回滚（无错误 Revision，无半截 TestCase） |
| 16 | 不自动触发重评审（保存仅标记 RE_REVIEW_REQUIRED，用户主动点击才 Review） |
| 17 | 重评审 trigger_type=AFTER_HUMAN_EDIT，review_revision+1 |
| 18 | 批量 re-review 接口预留（test_case_ids list，第一版内部统一 Review） |
| 19 | schema_version=9（test_case_revisions 表生效）+ V1 零回归 |

---

## 7. 关键决策记录（ADR）

- **D1 编辑白名单 + 系统字段保护**：防止前端越权修改 test_point_ids/fingerprint/id。Re-link TestCase 留未来单独设计。
- **D2 不自动重评审**：避免每次编辑都调 LLM（成本/延迟）。用户主动点击才 Review，不加配置项（太早）。
- **D3 Revision 保存修改前快照**：snapshot 是"修改前完整 TestCase"，不是修改后。这样才能恢复历史。
- **D4 revision_no vs review_revision 语义区分**：TestCaseRevision.revision_no=内容版本；ReviewReport.revision=评审版本。代码不混叫。
- **D5 fingerprint 不变 / content_hash 重算**：Step 5 双指纹设计的实际消费场景。identity 不变（同一 TestCase），content 变（内容改了）。
- **D6 编辑后必须过 Validator**：结构破坏（steps=[]）不应进入 AI Review。FAIL→VALIDATION_FAILED（可恢复）。
- **D7 VALIDATION_FAILED 可恢复**：状态机放开 VALIDATION_FAILED→EDITED，用户重新编辑修复，不是死状态。
- **D8 乐观锁 + 事务回滚**：updated_at 冲突→整体回滚，无错误 Revision，无半截 TestCase。比悲观锁简单。
- **D9 changed_fields 记录**：为 Step 10 Preference Learning 提供数据源（用户最常改什么字段）。
- **D10 provenance 语义分离**：Revision.provenance="这一版谁改的"；TestCase.provenance="当前生效版本来源"。

---

## 8. 诚实边界（第一版不做）

1. **不做前端 UI**：Step 13 前端 V2 才做，Step 9 仅提供 API/Python 接口。
2. **不引入 batch queue/parallel review/partial failure/retry**：批量接口仅 list 参数，内部统一 Review。
3. **不做 Re-link TestCase**（修改 test_point_ids）：留未来单独设计。
4. **不加 auto_review_on_edit 配置项**：太早，观察真实使用行为后再考虑。
5. **不做 Revision 差异对比 UI**：仅保存 snapshot + changed_fields，可视化留 Step 13。
6. **不做多用户协同编辑锁**：仅乐观锁（updated_at），不做悲观锁/编辑锁。

---

## 9. Step 9 完成后的系统变化

**AI + 代码 + 人 三者协同的完整测试设计流程**：

```
LLM 生成 (Step 3/4/5)
    ↓
代码验证 (Step 5 Validator)
    ↓
AI Review (Step 7)
    ↓
自动优化 (Step 8 Dedup Optimizer)
    ↓
人工修改 (Step 9 Human Editor)
    ↓
再次验证 (Step 9 Validator)
    ↓
再次 Review (Step 9 AFTER_HUMAN_EDIT)
```

**保持**：
- ✅ 不覆盖历史（Revision 快照保存修改前完整内容）
- ✅ 不破坏追溯链（test_point_ids 不可普通编辑）
- ✅ 不跳过验证（编辑后必须过 Validator）
- ✅ 不自动重评审（用户主动点击，控制 LLM 成本）
- ✅ 并发安全（乐观锁 + 事务回滚）
- ✅ 可审计（changed_fields + provenance 历史记录）
- ✅ 不碰 V1（core/db.py、web/、data.db 不动）

---

## 10. 验证命令（PowerShell）

```powershell
# 全量（期望 875 passed = Step 8 后 819 + Step 9 新增 56）
.\.venv\Scripts\python.exe -m pytest -q

# Step 9 专项（期望 59 passed = 30+7+22）
.\.venv\Scripts\python.exe -m pytest tests/test_human_editor.py tests/test_human_editor_orchestrator.py tests/test_step9_acceptance.py -v

# Lint
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

---

## 11. 下一步 = Step 10「Preference Learning」

见 `docs/v2/PROGRESS.md` §9。开工前需与用户讨论确认：
- 消费 Step 9 的 changed_fields 数据源（用户最常改什么字段）
- 偏好规则提取（EXTRACT_SYSTEM_PROMPT 升级）
- 偏好应用到 Prompt/生成策略
- Preference 表激活（reserved.py 已设计）
- 与 Step 11 Prompt 分层版本管理的边界
