# V2 Step 1 — 数据模型 / Schema 冻结设计文档

> 状态：**rev.2 — 待最终冻结**（已并入评审反馈 P0×6 + P1×5 + 细化项）
> 范围：冻结 V2 全量核心实体 Schema。Step 1 只定义"必须做"的实体并落地代码；"只设计不实现"的实体冻结形状但不建表、不写逻辑。
> 决策基线：SQLite 规范化拆表 + V1→V2 迁移 / Pydantic v2 唯一真源 / 原子需求项 + 全局稳定 ULID / 需求版本化 + 生成审计

**rev.2 修订摘要**：新增 `RequirementVersion`（需求版本）、`GenerationConfig`（模型/Prompt 版本审计）、`ReviewReport.revision`（评审多版本）、`TestCaseStatus` 生命周期状态机；去除 `CoverageObligation` 双重事实源；`TestCase.type` 枚举化；`ReviewScores` 模型化；新增置信度等级、多态目标 Domain Validator 约束；`FieldSpec` 增 `nullable`、`BusinessRule` 增 `expression_type`；预留 `TestScenario`/`TestCaseRevision`/`LLMInvocation`（只设计不实现）；明确 Step 1 实现范围与迁移"不猜历史关系"原则。

---

## 0. 设计原则（贯穿 V2）

核心数据流不再是 `Prompt → LLM → Result`，而是：

```
结构化数据 → 规则/策略 → LLM → 结构化数据 → Validator → Reviewer → 结构化数据
```

落到数据模型上的四条硬性要求：

1. **LLM 是大脑，不是控制器**：LLM 输入/输出两端都必须是**已定义 Schema 的结构化数据**；LLM 产物一律先过 Validator（代码）再入库。
2. **确定性关注点代码化**：边界值、等价类、重复检测、字段校验、Schema 校验、ID 生成、格式校验、必填项、权限矩阵、覆盖率 —— 由**数据模型 + 代码**承载，不写进 Prompt 靠模型自觉。
3. **全链路可追溯**：需求 → 版本 → 需求项 → 测试点 → 用例，任意一环可正/反查，且带稳定 ID 与外键。
4. **版本化与可审计**（rev.2 新增）：需求会变、模型/Prompt 会升级、评审是多轮过程、用例会被反复编辑。数据模型必须能回答"**这条用例当时基于哪个需求版本、用哪个模型/Prompt、经过几轮评审、被谁改过**"——这是 AI 测试平台做变更影响分析和 Benchmark 的前提。

---

## 1. V1 现状快照（冻结基线，不再改动）

### 1.1 SQLite 表（`core/db.py::init_db`）

| 表 | 关键列 | 存储方式 |
|---|---|---|
| `users` | id, username, password_hash, created_at | 规范化 |
| `sessions` | id, user_id, `requirement` TEXT, priority, case_types, **`testcases` TEXT(JSON)**, tc_count, **`review_report` TEXT(自由文本)**, is_deleted | 用例整体塞 blob |
| `session_images` | id, session_id, filename, media_type, data(base64), sort_order | 规范化 |
| `test_points` | id, user_id, title, `requirement` TEXT, **`points` TEXT(JSON)**, total | 测试点整体塞 blob |
| `preferences` | id, user_id, category, pattern, source_diff, weight, active | 规范化 |
| `preference_links` | id, preference_id, session_id, testcase_id | 松散关联 |
| `settings` | key, value(TEXT) | KV（模型配置，API Key 加密） |
| `materials` / `material_images` | — | 规范化 |

### 1.2 JSON 结构 & 管线

- 用例：`{id(TC_001), module, title, precondition, steps(换行文本), expected, priority(P0-P3), type(str), remark?}`
- 测试点：`[{module, subcategories:[{name, points:[{title, description}]}]}]`（旧格式无 subcategories，读取时 `_normalize_points_format()` 包装）
- 评审：LLM 自由文本，无结构
- 管线：需求+图片 →`analyze_modules()`(雏形IR，不持久化) →分模块并行`generate_for_module()` →`parse_response()`(容错JSON) →`validate_testcases()`(默认值+优先级枚举+ID格式+标点) →`deduplicate()`+`deduplicate_by_steps()`(Jaccard) →`limit_testcases()` →`review_testcases()`(文本) →导出

### 1.3 V1 → V2 要解决的结构性缺口

1. 无 Requirement IR（字段级信息丢失）；2. 无版本概念（需求一改，历史资产失去依据）；3. 无追溯链（test_points 与 sessions 不关联）；4. 评审非结构化、无多轮；5. 无生成审计（不知道用例是哪个模型/Prompt 产的）；6. 校验太轻 + blob 存储。

**V1 可复用**：代码级去重（Jaccard）、代码级 ID 生成、`parse_response` 容错解析、`validate_testcases`（Validator 雏形）。

---

## 2. V2 数据模型总览

### 2.1 实体关系（ER，rev.2）

```
User
 ├── Material / Asset（复用 V1）
 ├── Preference
 └── RequirementDoc（需求身份，稳定）
        │ 1:N
        ├── RequirementVersion（需求版本快照 v1/v2/v3）★P0-1
        │      │ 1:N
        │      └── RequirementItem（原子需求项 IR）
        │             ├── FieldSpec[]      （+nullable/format/example）
        │             ├── BusinessRule[]   （+expression_type）
        │             └── PermissionRule[]
        │
        └──（Run 绑定到具体 Version）

Run（一次生成会话）
 ├── requirement_version_id  ★P0-2（基于哪个版本生成）
 ├── GenerationConfig        ★P0-3（model/prompt/generator/reviewer 版本）
 ├── CoverageObligation[]    （策略引擎：代码算，item_id 关联 RequirementItem）
 ├── TestPoint[]  ──M:N(test_point_items)── RequirementItem
 ├── TestCase[]   ──M:N(test_case_points)── TestPoint
 │      └── status: TestCaseStatus 状态机 ★P0-6 / type: TestCaseType ★P1-1
 └── ReviewReport[]（revision 1/2/3…）★P0-4
        └── ReviewFinding[]（scores: ReviewScores ★P1-2；target 多态 ★P1-5）

覆盖（唯一事实源）★P0-5：
CoverageObligation ──obligation_coverage(target_type,target_id)──> TestPoint / TestCase
（satisfied_by_* 不持久化，由 @property 从 obligation_coverage 动态算）

设计预留（🟡 Step1 只冻结形状，不建表/不实现）：
TestScenario（RequirementItem→Scenario→TestPoint→TestCase，复杂业务流）
TestCaseRevision（用例多版本，PreferenceLearning 数据源）
LLMInvocation（每次 LLM 调用的 request/response/token/latency 审计）
```

### 2.2 实体清单

| 实体 | 角色 | Step | Step1 处理 |
|---|---|---|---|
| `RequirementDoc` | 需求身份（稳定） | 2 | ✅ 建表 |
| `RequirementVersion` | 需求版本快照 ★ | 2 | ✅ 建表 |
| `RequirementItem` | 原子需求项（IR） | 2/3 | ✅ 建表 |
| `FieldSpec` / `BusinessRule` / `PermissionRule` | 需求项值对象 | 2 | ✅（FieldSpec 建表，余 JSON） |
| `CoverageObligation` | 覆盖义务（代码算） | 4 | ✅ 建表 |
| `TestPoint` | 测试点 | 3 | ✅ 建表 |
| `TestCase` / `TestStep` | 测试用例 / 结构化步骤 | 5 | ✅ 建表 |
| `ReviewReport` / `ReviewFinding` / `ReviewScores` | 结构化多轮评审 | 7 | ✅ 建表 |
| `Run` | 一次生成会话 | 1 | ✅ 建表 |
| `GenerationConfig` | 生成审计配置 ★ | 1 | ✅ 建表 |
| `Preference` / `PreferenceLink` | 偏好学习 | 10 | ✅ 建表（沿用） |
| `Material` / `Asset` | 素材 / 二进制资源 | 1 | ✅ 建表 |
| `TestScenario` | 复杂业务流 | 5+ | 🟡 只设计 |
| `TestCaseRevision` | 用例多版本 | 9/10 | 🟡 只设计 |
| `LLMInvocation` | LLM 调用审计 | 全程 | 🟡 只设计 |

### 2.3 Step 1 实现范围（严格控制，防止过早复杂化）

**✅ 现在必须做**：RequirementVersion / Run 绑定 version+GenerationConfig / TestCaseType Enum / ReviewScores / ReviewReport revision / 去掉 CoverageObligation 双重关系 / 多态 target 的 Domain Validator / Pydantic Schema / SQLite DDL / Repository / V1 migration / migration tests。

**🟡 只设计不实现**（冻结形状，不建表、不写逻辑）：TestScenario / TestCaseRevision / LLMInvocation / PreferenceLearning 深度机制。

**❌ 暂时不碰**：Playwright / 自动执行 / Agent / RAG / 向量数据库 / 微服务 / PostgreSQL / Redis / 多 Agent。

> 最大风险不是功能少，而是**架构过早复杂化**。

---

## 3. 核心实体详解（Pydantic-first）

> 约定：所有持久化实体继承 `EntityBase`（`id: Ulid` + 时间戳 + `schema_version`）；枚举统一用 `StrEnum`（py3.11+，保证 `str(member)==value`，ruff UP042）；Pydantic `extra="forbid"` 严格模式。

### 3.1 通用：类型、枚举、状态机（`core/schemas/common.py`）

```python
Ulid = Annotated[str, StringConstraints(min_length=26, max_length=26)]  # 内置 ULID 实现（Q1：零依赖）

class Priority(StrEnum):
    P0="P0"; P1="P1"; P2="P2"; P3="P3"

class Provenance(StrEnum):
    """实体来源"""
    LLM="llm"; STRATEGY="strategy"; HUMAN="human"; VALIDATOR="validator"
    OPTIMIZER="optimizer"; MIGRATED="migrated"; XMIND="xmind"

class EntityStatus(StrEnum):
    """通用实体状态（Doc/Version/Item/Point 用）"""
    DRAFT="draft"; CONFIRMED="confirmed"; REJECTED="rejected"; ARCHIVED="archived"

class SourceType(StrEnum):
    MARKDOWN="markdown"; TEXT="text"; PDF="pdf"; WORD="word"
    IMAGE="image"; EXCEL="excel"; XMIND="xmind"

class TestCaseType(StrEnum):          # ★P1-1：取代 type:str
    FUNCTIONAL="functional"; BOUNDARY="boundary"; EQUIVALENCE="equivalence"
    EXCEPTION="exception"; PERMISSION="permission"; COMPATIBILITY="compatibility"
    PERFORMANCE="performance"; SECURITY="security"; STATE="state"; LINKAGE="linkage"

class TestCaseStatus(StrEnum):        # ★P0-6：用例生命周期状态机
    GENERATED="generated"; VALIDATED="validated"; VALIDATION_FAILED="validation_failed"
    REVIEWED="reviewed"; EDITED="edited"; RE_REVIEW_REQUIRED="re_review_required"
    CONFIRMED="confirmed"; ARCHIVED="archived"

# 状态机允许转移（代码强制，非法转移抛错）
ALLOWED_TRANSITIONS: dict[TestCaseStatus, set[TestCaseStatus]] = {
    TestCaseStatus.GENERATED:          {TestCaseStatus.VALIDATED, TestCaseStatus.VALIDATION_FAILED},
    TestCaseStatus.VALIDATION_FAILED:  {TestCaseStatus.GENERATED},        # 修复后重生成
    TestCaseStatus.VALIDATED:          {TestCaseStatus.REVIEWED},
    TestCaseStatus.REVIEWED:           {TestCaseStatus.CONFIRMED, TestCaseStatus.EDITED},
    TestCaseStatus.EDITED:             {TestCaseStatus.RE_REVIEW_REQUIRED},
    TestCaseStatus.RE_REVIEW_REQUIRED: {TestCaseStatus.REVIEWED},         # 必须重审
    TestCaseStatus.CONFIRMED:          {TestCaseStatus.ARCHIVED, TestCaseStatus.EDITED},
    TestCaseStatus.ARCHIVED:           set(),
}

class ConfidenceLevel(StrEnum):       # ★P1-4
    HIGH="high"; MEDIUM="medium"; LOW="low"

class ExpressionType(StrEnum):        # ★BusinessRule 可计算性
    NATURAL_LANGUAGE="natural_language"; FORMULA="formula"
    COMPARISON="comparison"; RANGE="range"; ENUM="enum"

class TestDimension(StrEnum):
    FUNCTIONAL="functional"; BOUNDARY="boundary"; EQUIVALENCE="equivalence"
    EXCEPTION="exception"; INTERACTION="interaction"; PERMISSION="permission"
    COMPATIBILITY="compatibility"; PERFORMANCE="performance"; SECURITY="security"
    STATE="state"; LINKAGE="linkage"; DATA_VALIDATION="data_validation"

class Technique(StrEnum):
    BOUNDARY_VALUE="boundary_value"; EQUIVALENCE_CLASS="equivalence_class"
    DECISION_TABLE="decision_table"; STATE_TRANSITION="state_transition"
    ERROR_GUESSING="error_guessing"; SCENARIO="scenario"; PERMISSION_MATRIX="permission_matrix"

class ReviewDimension(StrEnum):
    COVERAGE="coverage"; ACCURACY="accuracy"; EXECUTABILITY="executability"
    CONSISTENCY="consistency"; MISSING_RISK="missing_risk"; DUPLICATION="duplication"

class Severity(StrEnum):
    INFO="info"; MINOR="minor"; MAJOR="major"; CRITICAL="critical"

class TargetType(StrEnum):            # ★P1-5：多态目标类型
    TESTCASE="testcase"; TESTPOINT="testpoint"; OBLIGATION="obligation"
    REQUIREMENT_ITEM="requirement_item"

class ObligationStatus(StrEnum):
    PENDING="pending"; COVERED="covered"; WAIVED="waived"

class RunStatus(StrEnum):
    INGESTING="ingesting"; PARSING="parsing"; STRATEGIZING="strategizing"
    GENERATING="generating"; REVIEWING="reviewing"; DONE="done"; FAILED="failed"

class ReviewTriggerType(StrEnum):     # ★P0-4
    INITIAL="initial"; AFTER_OPTIMIZER="after_optimizer"
    AFTER_HUMAN_EDIT="after_human_edit"; MANUAL="manual"

class EntityBase(BaseModel):
    id: Ulid
    created_at: datetime
    updated_at: datetime
    schema_version: int = 2
    model_config = ConfigDict(extra="forbid")
```

### 3.2 需求层：Doc → Version → Item（`core/schemas/requirement.py`）

**RequirementDoc** — 需求身份（稳定，跨版本不变）
```python
class RequirementDoc(EntityBase):
    user_id: Ulid
    title: str
    source_type: SourceType
    asset_ids: list[Ulid] = []          # 图片/文件资源
    material_ids: list[Ulid] = []       # 引用的项目素材
    latest_version_id: Ulid | None = None   # 冗余指针，便于快速取当前版本
    status: EntityStatus = EntityStatus.DRAFT
```

**RequirementVersion** ★P0-1 — 需求版本快照（变更影响分析的基石）
```python
class RequirementVersion(EntityBase):
    doc_id: Ulid
    version_no: int                     # 1,2,3…（doc 内递增，唯一约束 (doc_id, version_no)）
    raw_text: str | None = None         # 本版需求全文快照
    change_summary: str | None = None   # 相对上版改了什么（人工/LLM 填）
    source_ref: SourceRef | None = None
    provenance: Provenance = Provenance.HUMAN
    status: EntityStatus = EntityStatus.DRAFT
```
> **语义**：需求"验证码 5 分钟"→"10 分钟"是**新增一个 Version**（v2），而非修改 v1。v1 下生成的历史用例仍指向 v1，历史依据不丢失；变更影响分析 = diff(v1.items, v2.items)。

**RequirementItem** — 原子需求项（IR 核心，隶属某个 Version）
```python
class RequirementItem(EntityBase):
    version_id: Ulid                    # ★ 改为隶属 Version（原 doc_id）
    seq: int                            # 版本内序号（RI-001 展示用）
    type: RequirementItemType
    module: str
    statement: str                      # 原子化单一意图
    fields: list[FieldSpec] = []        # 策略引擎输入
    rules: list[BusinessRule] = []
    permissions: list[PermissionRule] = []
    acceptance_criteria: list[str] = []
    source_ref: SourceRef | None = None # 追溯到版本原文/图片区域
    priority_hint: Priority | None = None
    confidence: float = 1.0             # [0,1] 抽取置信度
    confidence_level: ConfidenceLevel = ConfidenceLevel.HIGH   # ★P1-4，前端据此提示人工确认
    provenance: Provenance = Provenance.LLM
    status: EntityStatus = EntityStatus.DRAFT

class RequirementItemType(StrEnum):
    FUNCTION="function"; DATA_FIELD="data_field"; BUSINESS_RULE="business_rule"
    CONSTRAINT="constraint"; INTERACTION="interaction"; PERMISSION="permission"
    INTERFACE="interface"; NON_FUNCTIONAL="non_functional"
```

**FieldSpec** — 字段规格（★ 增 `nullable`；`format`/`example` 为增强项）
```python
class DataType(StrEnum):
    STRING="string"; INT="int"; FLOAT="float"; BOOL="bool"; DATE="date"
    DATETIME="datetime"; ENUM="enum"; EMAIL="email"; PHONE="phone"
    URL="url"; ID_CARD="id_card"

class FieldSpec(BaseModel):
    name: str
    label: str
    data_type: DataType
    required: bool = False              # 是否必填
    nullable: bool = False              # ★ 是否可为 null（required != nullable）
    min_length: int | None = None
    max_length: int | None = None
    min_value: float | None = None
    max_value: float | None = None
    pattern: str | None = None
    enum_values: list[str] = []
    precision: int | None = None
    unique: bool = False
    default: str | None = None
    unit: str | None = None
    format: str | None = None           # 🟡 增强项（如 "email"/"yyyy-MM-dd"）
    example: str | None = None          # 🟡 增强项
```

**BusinessRule** — 业务规则（★ 增 `expression_type` 支撑可计算性）
```python
class BusinessRule(BaseModel):
    name: str
    expression_type: ExpressionType = ExpressionType.NATURAL_LANGUAGE   # ★
    expression: str                     # 如 "age >= 18" / "total = price * quantity"
    inputs: list[str] = []              # 涉及字段
    expected: str | None = None
```
> **可计算性策略**：`expression_type ∈ {comparison, range, formula, enum}` 的规则，策略引擎**尝试代码求值**派生义务；`natural_language` 的交给 LLM。这是"能算的代码算，不能算的再给 LLM"的落点。

**PermissionRule / SourceRef**
```python
class PermissionRule(BaseModel):
    role: str; resource: str; action: str
    allowed: bool                       # 权限矩阵代码化的原子
    condition: str | None = None

class SourceRef(BaseModel):
    locator: str                        # paragraph/line/bbox
    value: str
    asset_id: Ulid | None = None
```

### 3.3 测试点（`core/schemas/testpoint.py`）

```python
class TestPoint(EntityBase):
    run_id: Ulid | None = None
    version_id: Ulid | None = None      # 冗余：便于按需求版本查测试点
    item_ids: list[Ulid] = []           # ★ 追溯：关联原子需求项（M:N → test_point_items）
    module: str
    subcategory: str
    title: str
    description: str
    dimension: TestDimension
    technique: Technique | None = None
    obligation_id: Ulid | None = None   # 若为覆盖某义务而生，回链
    priority: Priority = Priority.P1
    provenance: Provenance = Provenance.LLM
    status: EntityStatus = EntityStatus.DRAFT
```

### 3.4 测试用例（`core/schemas/testcase.py`）

```python
class TestStep(BaseModel):
    seq: int
    action: str
    data: str | None = None             # 为未来 Playwright/API 自动化留口
    expected: str | None = None

class TestCase(EntityBase):
    run_id: Ulid
    display_id: str                     # TC_001，run 内唯一，仅展示/导出
    test_point_ids: list[Ulid] = []     # ★ 追溯：M:N → test_case_points
    module: str
    title: str
    precondition: str = ""
    steps: list[TestStep]               # 结构化步骤（Q2）
    expected: str
    priority: Priority = Priority.P1
    type: TestCaseType                  # ★P1-1：枚举，非 str
    remark: str = ""
    fingerprint: str | None = None      # 去重指纹（代码算，Step 8）
    provenance: Provenance = Provenance.LLM
    status: TestCaseStatus = TestCaseStatus.GENERATED   # ★P0-6：状态机
    confidence_level: ConfidenceLevel = ConfidenceLevel.MEDIUM

    def transition_to(self, new: TestCaseStatus) -> None:
        """状态机：非法转移抛 ValueError（代码强制生命周期）"""
        if new not in ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(f"非法状态转移 {self.status} → {new}")
        self.status = new
```
> 导出层提供 `render_steps_text()` 生成 V1 风格换行文本，供 Excel/MD 与人工阅读。

### 3.5 覆盖义务（`core/schemas/strategy.py`）★P0-5 去双重事实源

```python
class CoverageObligation(EntityBase):
    """策略引擎按 RequirementItem（FieldSpec/rules/permissions）确定性推导的"必须覆盖项"。"""
    run_id: Ulid
    item_id: Ulid                       # 来源需求项
    technique: Technique
    target: str                         # 字段名/规则名/角色-资源
    description: str
    params: dict = {}                   # 结构化参数，如 {"min":1,"max":100}
    status: ObligationStatus = ObligationStatus.PENDING
    # ❌ 不再持久化 satisfied_by_points / satisfied_by_cases（双重事实源）
    # ✅ 唯一事实源 = obligation_coverage 关系表；下列为动态计算属性：
    @property
    def satisfied_by_points(self) -> list[Ulid]:
        return repo.coverage_targets(self.id, TargetType.TESTPOINT)
    @property
    def satisfied_by_cases(self) -> list[Ulid]:
        return repo.coverage_targets(self.id, TargetType.TESTCASE)
```

### 3.6 评审（`core/schemas/review.py`）★P0-4 多版本 + ★P1-2 ReviewScores

```python
class ReviewScores(BaseModel):          # ★P1-2：固定六维，取代 dict
    coverage: float
    accuracy: float
    executability: float
    consistency: float
    missing_risk: float
    duplication: float

class ReviewFinding(BaseModel):
    id: Ulid
    dimension: ReviewDimension
    severity: Severity
    target_type: TargetType             # ★P1-5：多态目标（无 DB 外键，Domain Validator 校验）
    target_id: Ulid
    issue: str
    suggestion: str | None = None
    provenance: Provenance              # validator(代码硬指标) / llm(软判断) / human
    auto_fixable: bool = False

class ReviewReport(EntityBase):
    run_id: Ulid
    revision: int                       # ★P0-4：1,2,3…（唯一约束 (run_id, revision)）
    trigger_type: ReviewTriggerType     # initial / after_optimizer / after_human_edit
    scores: ReviewScores
    overall_score: float
    summary: str
    obligation_coverage: float          # 覆盖率硬指标（代码算 = 已覆盖义务/总义务）
    # findings 存独立表 review_findings（report_id 外键）
```
> **多轮评审**：`Run → ReviewReport v1(initial) → v2(after_optimizer) → v3(after_human_edit)`。据此可回答"AI 优化到底有没有提升质量"——这正是未来 Benchmark 的数据来源。硬指标（覆盖率、重复、Schema 违规、必填缺失）由 Validator 产 `provenance=validator` 的 finding；语义/遗漏由 LLM 产 `provenance=llm`。

### 3.7 运行会话 + 生成审计（`core/schemas/run.py`）★P0-2/P0-3

```python
class GenerationConfig(BaseModel):      # ★P0-3：AI 生成审计（可复现/可评测的前提）
    model_provider: str                 # openai / anthropic / …
    model_name: str
    temperature: float
    max_tokens: int | None = None
    enable_thinking: bool | None = None
    prompt_version: str                 # 如 "case-generator-v3"
    generator_version: str              # 如 "2.1.0"
    reviewer_version: str | None = None # 如 "1.4.0"

class RunCounts(BaseModel):
    items: int = 0; points: int = 0; cases: int = 0; obligations: int = 0

class Run(EntityBase):
    user_id: Ulid
    doc_id: Ulid
    requirement_version_id: Ulid        # ★P0-2：明确基于哪个需求版本生成
    generation_config_id: Ulid          # ★P0-3：1:1 关联 GenerationConfig（独立表，便于复用/审计）
    strategy_profile: str | None = None
    status: RunStatus = RunStatus.INGESTING
    counts: RunCounts = RunCounts()
    legacy_session_id: int | None = None    # 迁移自 V1 时记录原 session.id
```

### 3.8 偏好（`core/schemas/preference.py`）

```python
class Preference(EntityBase):
    user_id: Ulid
    category: str
    pattern: str
    weight: float = 1.0
    active: bool = True
    source_diff: dict | None = None     # 未来由 TestCaseRevision 提供真实差异（🟡）
```

### 3.9 设计预留（🟡 Step 1 只冻结形状，不建表 / 不实现）

```python
# 🟡 TestScenario：复杂业务流（注册→登录→下单→支付→退款），单 TestCase 难以描述
class TestScenario(EntityBase):
    version_id: Ulid
    item_ids: list[Ulid] = []           # 关联需求项
    name: str
    flow: list[str]                     # 有序步骤概要
    test_point_ids: list[Ulid] = []     # Scenario → TestPoint → TestCase
    # 关系预留：RequirementItem → TestScenario → TestPoint → TestCase

# 🟡 TestCaseRevision：用例多版本（PreferenceLearning 的真实数据源）
class TestCaseRevision(EntityBase):
    test_case_id: Ulid
    revision: int                       # 1(LLM) → 2(Optimizer) → 3(Human) → …
    snapshot: dict                      # 该版本用例完整快照
    changed_fields: list[str]           # 相对上版改了哪些字段
    provenance: Provenance

# 🟡 LLMInvocation：每次 LLM 调用的完整审计（调试 AI 系统的关键）
class LLMInvocation(EntityBase):
    run_id: Ulid | None = None
    stage: str                          # parse/generate/review/optimize/preference
    provider: str; model: str; prompt_version: str
    request_json: dict                  # 输入（含 Prompt）
    response_json: dict | None = None   # 原始响应（保存但不作业务数据源）
    latency_ms: int | None = None
    token_usage: dict | None = None     # {prompt, completion, total}
    status: str                         # ok / error
    error: str | None = None
```
> **原则**：LLM 原始响应**保存但不作为业务数据源**——业务数据一律取 Parser+Validator 之后的结构化结果。出问题时可靠 `LLMInvocation` 复现“输入→Prompt→模型→Raw→Parser→Validator”全链，而非只能重新生成。

---

## 4. ID 与追溯链

### 4.1 ID 策略

- **主键**：全局稳定 **ULID**（26 字符 Crockford Base32，时间有序、无需协调）。内置 ~20 行实现，零依赖（Q1）。
- **展示 ID**：`display_id`（`TC_001`/`TP_001`/`RI_001`）仅 run/版本内唯一，用于导出/界面，**不作关联键**。
- 迁移：V1 `TC_001` 保留为 `display_id`，另发新 ULID 作主键。

### 4.2 追溯链（含版本，正/反向可查）

```
RequirementDoc ─1:N→ RequirementVersion ─1:N→ RequirementItem
                                                  │
              ┌─────────────────────┬──────────┴───────────┐
              ↓                          ↓                        ↓
     CoverageObligation ─obligation_coverage→ TestPoint ─test_case_points→ TestCase
              ↑                          ↑ test_point_items
              └───item_id───────────┘

Run ─→ requirement_version_id（基于哪个版本） + generation_config_id（用什么模型/Prompt）
ReviewReport(revision) ─→ ReviewFinding ─(target_type,target_id)→ TestCase/TestPoint/Obligation
```

- **正向**：需求项 → 义务/测试点 → 用例。
- **反向**：用例 → 测试点 → 需求项 → **需求版本** → 原文。
- **变更影响分析**（未来能力）：diff(Version v1.items, v2.items) → 定位受影响的需求项 → 沿追溯链找到需重生/复审的测试点与用例。
- **覆盖率硬指标**：`已满足义务/总义务`，由代码从 `obligation_coverage` 算（非 LLM 估计）。

---

## 5. 确定性 vs LLM 职责矩阵

| 关注点 | V2 归属 | 载体 |
|---|---|---|
| 需求理解/拆解/字段抽取 | LLM + 代码校验 | Parser→`RequirementItem`/`FieldSpec`，Pydantic 校验 |
| 需求版本管理/变更 diff | **代码** | `RequirementVersion` + 版本 diff |
| 边界值 / 等价类 | **代码** | StrategyEngine 读 `FieldSpec.min/max/enum` |
| 权限矩阵 | **代码** | `PermissionRule` 角色×资源×操作笛卡尔积 |
| 规则可计算求值 | **代码**(可算时) | `BusinessRule.expression_type ∈ {comparison,range,formula,enum}` |
| 覆盖义务/覆盖率 | **代码** | `CoverageObligation` + `obligation_coverage`（唯一事实源） |
| Schema/字段/必填校验 | **代码** | Pydantic `extra=forbid` + validators |
| ID 生成 | **代码** | ULID + display_id |
| 重复检测 | **代码** | `fingerprint` + 精确/语义去重 |
| 多态目标存在性 | **代码** | `TargetResolver` + Domain Validator（§6） |
| 状态机转移 | **代码** | `TestCaseStatus` + `ALLOWED_TRANSITIONS` |
| 用例文案生成 | LLM | Generator（消费 IR+义务） |
| 6维评审-硬指标 | **代码**(Validator) | `ReviewFinding(provenance=validator)` |
| 6维评审-软判断 | LLM 结构化 | `ReviewFinding(provenance=llm)` + `ReviewScores` |
| 生成审计 | **代码** | `GenerationConfig` / `LLMInvocation`（🟡） |

---

## 6. 多态目标与 Domain Validator 约束 ★P1-5

`ReviewFinding.target` 与 `obligation_coverage.target` 采用 `(target_type, target_id)` 的 **Polymorphic Association**。

**问题**：SQLite 外键无法约束“`target_type=testcase` 时 `target_id` 必须存在于 `test_cases`”——因为外键不知道 `target_id` 该指向哪张表。所以这是**逻辑关系，非 DB 强外键**。

**决策**：V2 先接受这个设计（不为它复杂化 schema），但**必须在代码层补足约束**：

```python
# core/engine/resolver.py
class TargetResolver:
    """target_type → 实体表的解析器"""
    _MAP = {
        TargetType.TESTCASE:  ("test_cases", TestCaseRepository),
        TargetType.TESTPOINT: ("test_points", TestPointRepository),
        TargetType.OBLIGATION:("coverage_obligations", ObligationRepository),
        TargetType.REQUIREMENT_ITEM: ("requirement_items", ItemRepository),
    }
    def resolve(self, target_type, target_id): ...
    def exists(self, target_type, target_id) -> bool: ...

class ReferentialValidator:
    """写入 ReviewFinding / obligation_coverage 前，校验 target 存在（DB 做不到，Domain 做）"""
    def validate(self, target_type, target_id) -> None:
        if not TargetResolver().exists(target_type, target_id):
            raise ReferentialIntegrityError(f"{target_type} {target_id} 不存在")
```

> 原则：**DB 无法约束 → Domain Validator 约束**。所有写入多态引用的代码路径必须过 `ReferentialValidator`（已写入测试用例）。

---

## 7. 置信度 / 数据可信等级 ★P1-4

不同来源的数据可信度不同，用 `confidence_level` 统一表达，前端据此提示人工确认（比静默生成错误用例好）：

| 来源 | 参考权重 | confidence_level |
|---|---|---|
| HUMAN_CONFIRMED | 1.0 | HIGH |
| CODE_VALIDATED | 0.95 | HIGH |
| LLM_HIGH_CONFIDENCE | 0.8 | MEDIUM |
| LLM_LOW_CONFIDENCE | 0.5 | LOW |
| MIGRATED | 0.3 | LOW |

- 权重仅作参考排序，**不当科学概率**；对外暴露的是 `confidence_level` 枚举。
- `RequirementItem.confidence_level=LOW` → 前端显示“⚠️ AI 对该需求项理解存在较大不确定性，请人工确认”。
- `confidence`（float）由 Parser 给出，`confidence_level` 由代码按阈值映射（可配）。

---

## 8. SQLite 物理表设计（DDL 草案）

> 原则：需**链接/查询/约束**的实体 → 独立表+外键；深层**值对象且无需独立查询** → Pydantic 校验的 JSON 列。
> `FieldSpec` 因策略引擎需跨项查询 → 独立表；`rules`/`permissions`/`steps`/`params` → JSON 列。
> 多态引用（target_type,target_id）**不建 DB 外键**，由 §6 Domain Validator 保障。

```sql
CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);   -- schema_version=2

-- 需求：Doc → Version → Item
CREATE TABLE requirement_docs (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
    title TEXT NOT NULL, source_type TEXT NOT NULL,
    latest_version_id TEXT, status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, schema_version INTEGER DEFAULT 2
);
CREATE TABLE requirement_versions (                       -- ★P0-1
    id TEXT PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES requirement_docs(id) ON DELETE CASCADE,
    version_no INTEGER NOT NULL, raw_text TEXT, change_summary TEXT,
    source_ref_json TEXT, provenance TEXT NOT NULL DEFAULT 'human',
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, schema_version INTEGER DEFAULT 2,
    UNIQUE (doc_id, version_no)
);
CREATE TABLE requirement_items (
    id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES requirement_versions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL, type TEXT NOT NULL, module TEXT NOT NULL, statement TEXT NOT NULL,
    rules_json TEXT, permissions_json TEXT, acceptance_json TEXT, source_ref_json TEXT,
    priority_hint TEXT, confidence REAL DEFAULT 1.0, confidence_level TEXT DEFAULT 'high',
    provenance TEXT NOT NULL DEFAULT 'llm', status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, schema_version INTEGER DEFAULT 2
);
CREATE INDEX idx_items_version ON requirement_items(version_id);
CREATE TABLE field_specs (
    id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES requirement_items(id) ON DELETE CASCADE,
    name TEXT NOT NULL, label TEXT NOT NULL, data_type TEXT NOT NULL,
    required INTEGER NOT NULL DEFAULT 0, nullable INTEGER NOT NULL DEFAULT 0,   -- ★
    min_length INTEGER, max_length INTEGER, min_value REAL, max_value REAL,
    pattern TEXT, enum_values_json TEXT, precision INTEGER, unique_flag INTEGER DEFAULT 0,
    default_value TEXT, unit TEXT, format TEXT, example TEXT
);
CREATE INDEX idx_fields_item ON field_specs(item_id);

-- 运行 + 生成审计
CREATE TABLE runs (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
    doc_id TEXT NOT NULL REFERENCES requirement_docs(id),
    requirement_version_id TEXT NOT NULL REFERENCES requirement_versions(id),   -- ★P0-2
    generation_config_id TEXT NOT NULL REFERENCES generation_configs(id),       -- ★P0-3
    strategy_profile TEXT, status TEXT NOT NULL DEFAULT 'ingesting',
    counts_json TEXT, legacy_session_id INTEGER,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, schema_version INTEGER DEFAULT 2
);
CREATE TABLE generation_configs (                           -- ★P0-3
    id TEXT PRIMARY KEY,
    model_provider TEXT NOT NULL, model_name TEXT NOT NULL, temperature REAL NOT NULL,
    max_tokens INTEGER, enable_thinking INTEGER,
    prompt_version TEXT NOT NULL, generator_version TEXT NOT NULL, reviewer_version TEXT,
    created_at TEXT NOT NULL
);

-- 测试资产
CREATE TABLE test_points (
    id TEXT PRIMARY KEY, run_id TEXT, version_id TEXT,
    module TEXT NOT NULL, subcategory TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
    dimension TEXT NOT NULL, technique TEXT, obligation_id TEXT,
    priority TEXT NOT NULL DEFAULT 'P1', provenance TEXT NOT NULL DEFAULT 'llm',
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, schema_version INTEGER DEFAULT 2
);
CREATE TABLE test_cases (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    display_id TEXT NOT NULL, module TEXT NOT NULL, title TEXT NOT NULL,
    precondition TEXT DEFAULT '', steps_json TEXT NOT NULL, expected TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'P1', type TEXT NOT NULL,               -- ★P1-1 TestCaseType
    remark TEXT DEFAULT '', fingerprint TEXT,
    provenance TEXT NOT NULL DEFAULT 'llm', status TEXT NOT NULL DEFAULT 'generated',  -- ★P0-6
    confidence_level TEXT DEFAULT 'medium',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, schema_version INTEGER DEFAULT 2
);
CREATE INDEX idx_cases_run ON test_cases(run_id);
CREATE INDEX idx_cases_fp ON test_cases(fingerprint);
CREATE TABLE coverage_obligations (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, item_id TEXT NOT NULL REFERENCES requirement_items(id),
    technique TEXT NOT NULL, target TEXT NOT NULL, description TEXT NOT NULL,
    params_json TEXT, status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, schema_version INTEGER DEFAULT 2
);

-- 评审（多版本）
CREATE TABLE review_reports (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL, trigger_type TEXT NOT NULL,                -- ★P0-4
    scores_json TEXT NOT NULL,                                            -- ★P1-2 ReviewScores
    overall_score REAL NOT NULL, summary TEXT, obligation_coverage REAL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, schema_version INTEGER DEFAULT 2,
    UNIQUE (run_id, revision)
);
CREATE TABLE review_findings (
    id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES review_reports(id) ON DELETE CASCADE,
    dimension TEXT NOT NULL, severity TEXT NOT NULL,
    target_type TEXT NOT NULL, target_id TEXT NOT NULL,                   -- ★P1-5 多态，无外键
    issue TEXT NOT NULL, suggestion TEXT, provenance TEXT NOT NULL, auto_fixable INTEGER DEFAULT 0
);
CREATE INDEX idx_findings_target ON review_findings(target_type, target_id);

-- 追溯链接表（M:N）
CREATE TABLE test_point_items (
    test_point_id TEXT NOT NULL REFERENCES test_points(id) ON DELETE CASCADE,
    requirement_item_id TEXT NOT NULL REFERENCES requirement_items(id) ON DELETE CASCADE,
    PRIMARY KEY (test_point_id, requirement_item_id)
);
CREATE TABLE test_case_points (
    test_case_id TEXT NOT NULL REFERENCES test_cases(id) ON DELETE CASCADE,
    test_point_id TEXT NOT NULL REFERENCES test_points(id) ON DELETE CASCADE,
    PRIMARY KEY (test_case_id, test_point_id)
);
CREATE TABLE obligation_coverage (                          -- ★P0-5 覆盖唯一事实源
    obligation_id TEXT NOT NULL REFERENCES coverage_obligations(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL,                              -- testpoint/testcase（多态，无外键）
    target_id TEXT NOT NULL,
    PRIMARY KEY (obligation_id, target_type, target_id)
);
```

> `users`/`materials`/`assets`/`preferences` 沿用 V1 思路（主键改 ULID、加 `schema_version`）；`session_images`/`material_images` 合并为 `assets(id, owner_type, owner_id, filename, media_type, data, sort_order)`。
> 🟡 **只设计不建表**：`test_scenarios`、`test_case_revisions`、`llm_invocations`（Step 1 冻结形状，后续 Step 再建）。

---

## 9. V1 → V2 迁移

### 9.1 迁移铁律 ★（评审强调）

> **绝不为“完整追溯”而让 AI 猜历史关系。** 无法确定的 = NULL。

```
V1 数据迁移 → provenance = migrated → 追溯关系“尽可能建立” → 无法确定的 = NULL
```

- V1 `test_points` 与 `sessions` **本就分离**，迁移时**不强行推断** TestPoint → Run 关系（除非 DB 里有明确关联）。
- V1 无需求版本概念 → 每个迁移的 RequirementDoc 只生一个 `RequirementVersion(version_no=1, provenance=migrated)`。
- V1 无生成审计 → `GenerationConfig` 字段能填则填（从 settings 取当前模型），不能填则置“unknown”，**不编造**。

### 9.2 映射表

| V1 来源 | V2 目标 | 处理 |
|---|---|---|
| `sessions.requirement` | `RequirementDoc` + `RequirementVersion(v1)` + `Run` | `legacy_session_id` 记原 id；Run 绑定 v1 |
| `sessions.testcases`(JSON) | `TestCase` | `TC_001`→`display_id`，新发 ULID；steps 文本→`TestStep[]`；`type` 映射→`TestCaseType`；`status=generated`；`provenance=migrated` |
| `sessions.review_report`(文本) | `ReviewReport(revision=1,trigger=initial).summary` | 自由文本无法结构化，findings 空，标注 legacy |
| `test_points.points`(JSON) | `TestPoint` | 展开为独立行；`item_ids`/`run_id` 留空（不猜） |
| `settings` | `settings`（沿用）+ 派生 `GenerationConfig` | 模型配置保留；审计字段缺失置 unknown |
| `preferences` / `preference_links` | `Preference` / `PreferenceLink` | 直接映射，主键换 ULID |
| `*_images` | `assets` | 合并，`owner_type/owner_id` 指向 run/material |

### 9.3 迁移规模实测（当前 `data/data.db`）

低风险一次性操作：users 8 / sessions 7（均含非空用例，共 ~80+ 条）/ test_points 3（~85 点）/ preferences 1 / materials 5 / material_images 6 / review_report 仅 1 条非空。单条最大 session：23 用例 / blob 8.3KB。

**迁移步骤**：备份 db → 建新表（`schema_meta=2`）→ 跑 `migrate_v1_to_v2.py`（读 blob、Pydantic 校验、写新表、建可确定的链接）→ 校验计数一致 → 旧表只读保留一段（灰度）→ 确认后归档。

---

## 10. 代码组织（包结构）

> **实现级决策（编码时发现，已定）**：
> 1. `core/db.py` 已是 V1 模块，Python 不允许同时存在 `core/db/` 包（会 shadow V1、破坏运行时）→ V2 持久层用 **`core/v2/`** 包。
> 2. V1 `users`/`preferences`/`test_points` 为 INTEGER 主键且表名与 V2 冲突 → V2 用**独立数据库 `data/data_v2.db`**（自带 ULID 主键的 users 表）。V1 db 完全不动、零风险、可整体回滚（删文件即可）；迁移 = 读 V1 db 写 V2 db。V2 稳定后再切换为主库。

```
core/
  schemas/                 # ★ Pydantic 模型（唯一真源）
    common.py              #   EntityBase / 全部枚举 / TestCaseStatus 状态机 / Ulid
    requirement.py         #   Doc/Version/Item/FieldSpec/BusinessRule/PermissionRule/SourceRef
    testpoint.py testcase.py strategy.py review.py run.py preference.py
    reserved.py            #   🟡 TestScenario/TestCaseRevision/LLMInvocation（只定义）
  v2/                      # ★ V2 持久层 + 领域服务（独立 data_v2.db，与 V1 隔离）
    db.py                  #   V2 连接管理（WAL/foreign_keys/写锁）+ set_v2_db_path
    ddl.py                 #   建表 SQL + schema_version 管理
    repository.py          #   Pydantic ↔ SQLite 映射（写 model_dump，读 model_validate）
    resolver.py            #   ★ TargetResolver + ReferentialValidator（§6 多态目标）
    migrate_v1_to_v2.py    #   迁移脚本（读 V1 db → 写 V2 db，遵 §9.1 铁律）
  engine/                  # （后续 Step，Step1 不实现）
    parser.py strategy.py validator.py dedup.py reviewer.py
```

---

## 11. 端到端示例（需求变更场景，验证版本化）

> 用你提的“验证码 5→10 分钟”场景，验证 RequirementVersion 如何保住历史资产。

**阶段一：v1 需求**“验证码 5 分钟有效”
```jsonc
RequirementDoc   { id:"REQ-001", title:"登录模块" }
RequirementVersion{ id:"REV-001", doc_id:"REQ-001", version_no:1, raw_text:"验证码5分钟有效" }
RequirementItem  { id:"RI-001", version_id:"REV-001", type:"data_field",
                   fields:[{name:"code_expiry_min", data_type:"int", min_value:5, max_value:5}] }
GenerationConfig { id:"GC-01", model_name:"gpt-5.6", prompt_version:"case-generator-v3",
                   generator_version:"2.1.0", temperature:0.2 }
Run              { id:"RUN-001", requirement_version_id:"REV-001", generation_config_id:"GC-01" }
CoverageObligation{ id:"OB-01", item_id:"RI-001", technique:"boundary_value",
                   target:"code_expiry", params:{"min":5,"max":5} }   // 代码算边界 4:59/5:00/5:01
TestCase { id:"TC…01", display_id:"TC_001", type:"boundary", status:"confirmed",
           title:"验证码4:59可用", test_point_ids:["TP-01"] }        // → TC_002 "5:01失效"
ReviewReport{ id:"RR-01", run_id:"RUN-001", revision:1, trigger_type:"initial",
           scores:{coverage:95,accuracy:90,executability:88,consistency:95,missing_risk:80,duplication:98},
           obligation_coverage:1.0 }
```

**阶段二：产品改需求 → 新增 v2（而非修改 v1）**“验证码 10 分钟”
```jsonc
RequirementVersion{ id:"REV-002", doc_id:"REQ-001", version_no:2,
                   raw_text:"验证码10分钟有效", change_summary:"有效期 5→10 分钟" }
RequirementItem  { id:"RI-002", version_id:"REV-002", fields:[{name:"code_expiry_min",min_value:10,max_value:10}] }
Run              { id:"RUN-002", requirement_version_id:"REV-002", generation_config_id:"GC-02" }
// 新义务 OB-02 边界 9:59/10:00/10:01 → 新用例
```

**变更影响分析（代码，非 LLM）**：
- `diff(REV-001.items, REV-002.items)` → 定位 `code_expiry_min` 变更
- 沿追溯链：`RI-001 → OB-01 → TP-01 → TC_001/TC_002` 均基于 v1
- **历史资产不丢**：TC_001/TC_002 仍指向 REV-001，依据完整；系统标记它们“基于旧版本，需复审”而不删除。

> 全链中：**边界值/覆盖率/变更 diff/状态机/多态校验全是代码**；LLM 只负责拆解需求与生成用例文案；审计链（GC-01/gpt-5.6/prompt-v3）完整可回答“TC_001 当时是哪个模型生成的”。

---

## 12. 开放问题（已根据评审收敛）

| # | 问题 | 结论 |
|---|---|---|
| Q1 | ULID 依赖 | **内置 ~20 行实现**，零依赖 |
| Q2 | steps 结构 | **结构化 `TestStep[]`** + 导出时 render 文本 |
| Q3 | FieldSpec 存储 | **独立表**（策略引擎需跨项查询）；rules/permissions 仍 JSON |
| Q4 | 旧评审文本 | 存 `ReviewReport.summary`，findings 空 |
| Q5 | 旧数据回填 IR | 可选功能，默认不自动猜（遵 §9.1） |
| Q6 | Doc:Run 关系 | 改为 **Version:Run = 1:N**（Doc:Version=1:N） |

---

## 13. Step 1 验收标准

1. 本文档 rev.2 评审通过（P0×6 + P1×5 已并入）。
2. `core/schemas/*.py`（Pydantic v2）：含 RequirementVersion/GenerationConfig/TestCaseType/ReviewScores/TestCaseStatus 状态机；`model_validate` 能校验合法/非法样例；非法状态转移报错。
3. `core/db/ddl.py` + `repository.py`：建表与映射，`obligation_coverage` 为覆盖唯一事实源（satisfied_by_* 为 @property）。
4. `core/engine/resolver.py`：`TargetResolver` + `ReferentialValidator` 对多态 target 做写入前校验。
5. `migrate_v1_to_v2.py`：遵循§9.1 铁律（不猜历史关系，未知=NULL），对现有 `data.db` 迁移后计数一致、旧数据可读。
6. `tests/test_schemas.py` + `tests/test_migration.py` + `tests/test_state_machine.py` + `tests/test_resolver.py` 全绿。
7. 🟡 只设计不实现的（TestScenario/TestCaseRevision/LLMInvocation）仅在 `reserved.py` 定义形状，不建表、不接入管线。

**下一步**：Step 2「建立 Requirement IR」——实现 Parser（LLM 抽取→Version/Item/FieldSpec，代码校验）。

---

## 14. 评审反馈对照表（20 点 → 文档位置）

| 反馈点 | 处理 | 位置 |
|---|---|---|
| 一 RequirementVersion | ✅ 新增实体，Doc→Version→Item | §3.2 / §8 |
| 三 Run→Doc 太弱 | ✅ Run 增 `requirement_version_id` | §3.7 / §8 |
| 三 模型/Prompt 审计 | ✅ 新增 `GenerationConfig`（provider/model/temp/prompt/generator/reviewer version） | §3.7 / §8 |
| 四 ReviewReport 多份 | ✅ 增 `revision` + `trigger_type`，UNIQUE(run_id,revision) | §3.6 / §8 |
| 五 状态机太简单 | ✅ `TestCaseStatus` 8 态 + `ALLOWED_TRANSITIONS` + `transition_to()` | §3.1 / §3.4 |
| 六 obligation 双重事实源 | ✅ 删 satisfied_by_* 字段，`obligation_coverage` 唯一源 + @property | §3.5 / §8 |
| 多态 target 无外键 | ✅ 新增 Domain Validator 章节（TargetResolver/ReferentialValidator） | §6 |
| 九 FieldSpec nullable | ✅ 增 `nullable`（+`format`/`example` 增强项） | §3.2 |
| 十 BusinessRule 可计算性 | ✅ 增 `expression_type`（formula/comparison/range/enum/nl） | §3.2 |
| 十一 TestScenario | 🟡 只设计预留，不实现 | §3.9 |
| 十二 TestCase.type Enum | ✅ `TestCaseType` 枚举取代 str | §3.1 / §3.4 |
| 十三 ReviewScores Model | ✅ 固定六维模型取代 dict | §3.6 |
| 十四 LLM 原始响应留存 | 🟡 `LLMInvocation` 只设计（保存但不作业务数据源） | §3.9 |
| 十五 Artifact Revision | 🟡 `TestCaseRevision` 只设计 | §3.9 |
| 十六 迁移不猜历史关系 | ✅ 写入迁移铁律（未知=NULL，provenance=migrated） | §9.1 |
| 十七 数据可信等级 | ✅ `ConfidenceLevel` + 参考权重表 + 前端提示 | §7 / §3.1 |
| 十八 最终实体层级 | ✅ ER 按此重构 | §2.1 |
| 十九 范围控制 | ✅ 必做/只设计/不碰 三档 | §2.3 |
| 二十 验收意见 | ✅ rev.2 作为基线，P0+P1 已并入，待你确认后冻结 | 全文 |

> **保留的核心**：不是“拆了多少表”，而是“LLM 负责理解与生成，代码负责约束、策略、覆盖率与一致性”这个原则已真正落到数据模型里。
