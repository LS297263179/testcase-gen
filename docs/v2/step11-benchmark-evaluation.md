# V2 Step 11 — Benchmark 与质量评价基础设施（冻结设计 v1.0）

> 状态：**冻结基线**（S1 产物）。本文档 = 《Step 11 最终技术实施方案 v1.0》+ 实施前核查结论的正式落地文本。
> 方案基线 HEAD：`d1c5345`。本阶段不得擅自改变 Step 11 的总体架构、边界和核心指标定义。
> 范围：Quality Evaluation Foundation + Benchmark Foundation。

---

## §0 一句话目标

建立 V2 第一套**可重复、可比较、可回归**的 AI 测试质量评价基础设施。

最终目标链路（冻结，Step 11 的完成判据是真实产出 baseline，而非仅写完脚本）：

```
Benchmark Case
  → scripts/v2_benchmark.py
  → run_v2_pipeline(...)          （V2 原有 Runtime，零修改）
  → V2 原有产物（IR/TP/TC/Obligation/ReviewReport，落在独立 benchmark DB）
  → Evaluator（core/v2/eval，Runtime 外部观测层）
  → Report
  → Baseline v0.1
```

---

## §1 总体目标与定位

1. **评价层位于 Runtime 外部**：Benchmark 是独立观测层，不 Hook、不侵入、不改变任何生成行为。
2. **可重复**：同一 Case + 同一环境指纹重跑，结论可复现（LLM 非确定性由 repeat 次数与报告口径吸收）。
3. **可比较**：任何一次 runset 能与 baseline 做 delta 对比（improvement / regression / unchanged）。
4. **可回归**：为未来 Prompt / 模型 / 策略升级提供质量标尺（第一版不做 CI 硬门禁，见 §11）。

---

## §2 架构边界（冻结 18 条）

| # | 冻结规则 |
|---|---|
| 1 | 不修改 V1 |
| 2 | 不修改 V2 Runtime 行为 |
| 3 | 不修改 `core/v2/runtime.py` |
| 4 | 不修改任何 `*_orchestrator.py` |
| 5 | 不修改现有 14 个 V2 API（`web/v2_routes.py`） |
| 6 | 不修改 `frontend-v2` |
| 7 | 不修改 `templates/v2.html` |
| 8 | 不修改 6 个现有 Prompt 文件 |
| 9 | 不修改 `core/schemas`（只读 import 复用枚举属允许行为） |
| 10 | schema_version 保持 10（含义见 §10.2） |
| 11 | 不新增 V2 DB 表 |
| 12 | 不加入 Playwright |
| 13 | 不加入 API 自动执行 |
| 14 | 不加入 Preference Learning |
| 15 | 不做 Prompt Optimization |
| 16 | 不新增第二套 LLM Reviewer Prompt |
| 17 | 不做强 CI Regression Gate |
| 18 | Benchmark 使用独立 benchmark DB，不污染 `data/data_v2.db`（见 §12） |

**S1 + S2 文件影响**：全部为新增（本文档 + `core/v2/eval/` + `benchmark/` 目录与示例 + 单测），零修改、零删除。

---

## §3 Gold Standard 独立性声明（冻结）

1. **Gold 必须由人工独立分析原始需求产生**：`原始需求 → 人工独立分析 → Gold`。
2. 允许参考现有 V2 输出（含 Step 10.5 真实运行的 174 TP / 174 TC），但**只能作为整理素材**（reference material），落盘于 `reference_cases` 字段并注明来源。
3. **禁止闭环**："V2 自己生成 → 从结果筛选 → 直接作为 Gold" 一律无效。
4. 机检手段：`BenchmarkGold.gold_authoring` 为枚举，**正式态必须为 `human_independent`**；
   `human_assisted`（整理素材后经人工逐条确认）为过渡态，loader 默认拒绝其进入正式评价（可显式放行用于素材整理）。
5. **Gold 是最低必要集合（floor），不是完整答案全集（ceiling）**：
   - AI 生成 Gold 之外的合理测试，不因"不在 Gold 中"直接判错；
   - 真正的错误重点是 miss / invalid / forbidden / unsupported / duplication；
   - **TP 数量不设统一门槛**（核查结论 6）：完成度以 obligation / critical_scenario 覆盖为核心，不以"点够不够多"计分。

---

## §4 Matching 规则（冻结）

### §4.1 RequirementItem 四态匹配

| 状态 | 判定依据 | 计分 |
|---|---|---|
| `AUTO_HIT` | **identity fingerprint 精确相等**（确定性，复用 `core/v2/fingerprint.compute_item_identity_fingerprint` 重算） | ✅ 命中 |
| `CANDIDATE` | 相似度达到提示区间 | ❌ 不计分，进 `pending_review` |
| `MISS` | 无任何 AUTO_HIT / CANDIDATE 对应 | 计为缺失 |
| `CONFLICT` / `AMBIGUOUS` | 一对多 / 多对一歧义，无法可靠归属 | ❌ 不自动计分，进 `pending_review` |

规则（冻结；S3 修正 2 落地细化）：

1. identity fingerprint → 唯一可产生确定性 AUTO_HIT 的依据；
   **优先级：Gold 显式 match_keys.identity_fingerprints > statement 重算（仅无 match_keys 时的兼容 fallback）> similarity**；
   重算不得覆盖 Gold 中显式 match_keys；
2. content hash → **只用于内容偏差诊断**（identity 同内容异的 MODIFIED 观察），不参与命中；
3. similarity → **只能产生 CANDIDATE / 辅助诊断**，禁止"相似度≥阈值即命中"；
4. CANDIDATE / AMBIGUOUS 一律不自动计分，进 `pending_review` 清单由人工裁决；
5. **TestPoint 不使用文本相似度判定 Gold 命中**，优先依赖结构化关系：
   `TestPoint.obligation_id` ↔ obligation、`test_point_items` ↔ RequirementItem、`test_case_points` ↔ TestCase。

### §4.2 Gold 侧 AUTO_HIT 的可书写性

`ri_` fingerprint 公式为 `sha256(normalize(module) | type | normalize(statement))`，normalize 仅折叠空白与大小写。
Gold 编写守则：**statement / module 尽量原文照抄需求表述**（与 Step 2 parser 的 source_ref"原文照抄"要求同源），
降低人工复刻偏差；AUTO_HIT 未命中会自然落入 CANDIDATE/MISS 并由 pending_review 吸收，不做惩罚性判分。

---

## §5 Strategy Gold：条件触发（冻结）

不使用"每个 RequirementItem 至少 N 个 TP"的数量门槛。
改为**需求具备什么测试特征，就要求什么策略**：`strategy_expectations: [{feature, technique, requirement_gold_id}]`。

`StrategyFeature` 冻结枚举与 Step 4 策略引擎**实际能力**一一对应（不伪造 Step 4 尚未支持的策略）：

| feature | technique | Step 4 依据 |
|---|---|---|
| `numeric_range` | `boundary_value` | FieldSpec.min_value/max_value 六点边界 |
| `length_range` | `boundary_value` | FieldSpec.min_length/max_length |
| `enum_field` | `equivalence_class` | enum_values 合法类 + 非法类 |
| `pattern_field` | `equivalence_class` | FieldSpec.pattern |
| `required_field` | `equivalence_class` | required 提供/缺失两类 |
| `nullable_field` | `equivalence_class` | nullable 语义 |
| `unique_field` | `equivalence_class` | unique 约束 |
| `typed_format_field` | `equivalence_class` | data_type ∈ {email, phone, url, id_card} |
| `permission_rule` | `permission_matrix` | PermissionRule role×resource×action |

`DECISION_TABLE / STATE_TRANSITION / ERROR_GUESSING / SCENARIO` 为 Step 4 明确延后能力：
**不得**进入 `strategy_expectations`；状态流转类考察 LLM 语义设计能力，由 `critical_scenarios` 承担（见 §8）。
feature→technique 映射由 schema 校验器强制（写错映射的 Gold 直接 fail）。

---

## §6 Metrics 手册（核心指标冻结）

| # | 指标 | 口径 | 计算轨 | Provenance |
|---|---|---|---|---|
| 1 | **Critical Scenario Coverage** | 每个 critical_scenario 按结构化 anchor 判定覆盖（§8），第一优先指标 | GOLD-BASED | CODE |
| 2 | Requirement Coverage | AUTO_HIT 命中的 GoldRequirement 数 / Gold 总数 | GOLD-BASED | CODE |
| 3 | Obligation Coverage | obligation_coverage 关系表已覆盖义务 / 总义务（复用 `obligation_coverage_ratio` 口径） | GOLD-BASED | CODE |
| 4 | Unsupported / Invalid | 三态分计（见下），**不得混成单一模糊指标** | GOLD-BASED | CODE+LLM |
| 5 | Structural Validity | steps 非空、action/expected 齐、status 合法、schema 全过 | 双轨均适用 | CODE |
| 6 | Missing Risk | Gold 有而产物无的 obligation / scenario 明细 | GOLD-BASED | CODE |
| 7 | Semantic Accuracy | LLM 判断"用例语义是否与其 trace 的 Gold 需求一致" | 见 §7 双轨 | LLM |
| 8 | Duplication | fingerprint 精确重复（CODE）+ 语义相似候选（LLM/CANDIDATE） | 双轨 | MIXED |
| 9 | Executability | 结构分 + 语义分（沿用 Step 7 加权 0.4/0.6 口径观察，不重造） | REVIEWER-BASED 复用 | MIXED |
| 10 | Cost / Latency | 读 PipelineResult（total_duration_ms / total_llm_calls / per-step duration），**同进程采集，不加 DB 字段** | 运行观测 | CODE |

辅助观察（保留但**不作为唯一核心质量依据**）：Requirement Precision、TestPoint Precision、
Additional Valid Count、TP/TC 数量。再次强调：**Gold 是 floor 不是 ceiling**。

### §6.1 Unsupported / Invalid 三态（冻结）

| 态 | 定义 | 后果 |
|---|---|---|
| `INVALID` | 代码可明确判错（结构非法 / 命中 forbidden_patterns / trace 断链） | 计错 |
| `PENDING_REVIEW` | 无法自动可靠判断（CANDIDATE / AMBIGUOUS / LLM 低置信） | 不计分，人工裁决 |
| `ADDITIONAL_VALID` | Gold 之外的合理新增，经人工确认有效 | 不判错，正向观察 |

---

## §7 GOLD-BASED 与 REVIEWER-BASED 双轨（冻结）

- **GOLD-BASED** = 是否符合人工独立定义的标准（Gold 文件）。
- **REVIEWER-BASED** = 现有 V2 AI Reviewer 如何评价（直接读 ReviewReport：scores / coverage_detail / executability_detail / findings）。

规则：

1. 两轨**并列展示，不互相作为裁判，不混算**；
2. 每个指标标注 provenance，`MetricProvenance` 为 **eval 私有枚举：`CODE / LLM / MIXED`**（核查结论 3；
   与 `core/schemas.common.Provenance` 是两套枚举，互不引用、互不改写）；
3. **不新增第二套 LLM Reviewer Prompt**——REVIEWER-BASED 轨道只消费现有 Step 7 产物；
   评价层自身如需 LLM 语义判断（如 Semantic Accuracy），是独立的评价用途调用，不得伪装成"第二评审"，且必须在报告标注 `LLM` provenance。

---

## §8 Critical Scenario 判定原则（冻结）

不能仅靠关键词撞到就算覆盖。必须按**结构化 anchor** 判定，核心结构锚点：

1. 对应 RequirementItem（`requirement_gold_ids` 且该 Gold 项 AUTO_HIT）；
2. 对应 technique / obligation（`expected_techniques` 与产物 TestPoint.technique / obligation 关系可判时）；
3. 对应动作或 expected 结果（`expected_actions` / `expected_outcomes` 与 TestCase 结构化 steps 对齐）。

核心结构锚点必须全部满足才计 HIT；任何一环无法自动可靠判断的 scenario → `AMBIGUOUS` → 进人工复核清单，不强行计分。

---

## §9 质量与运行可靠性分离（冻结）

### §9.1 Run Status 五态（核查结论 2）

`BenchmarkRunStatus` 为 **eval 私有枚举**（不与 `common.RunStatus` 混用，不改 DB）：

`COMPLETED / LLM_FAILURE / RUNTIME_FAILURE / EVALUATION_FAILURE / INPUT_INVALID`

- 非 COMPLETED 的 case **不进质量指标分母**（不得简单折算质量 0 分）；
- 单独统计：Completion Rate、Runtime Failure Rate、Evaluation Failure Rate、Input Invalid Rate；
- 所有质量指标必须注明 `evaluated_cases=n/N`。

### §9.2 失败传播现状核查结论（实施前检查，冻结为设计依据）

现有 Runtime **不满足**机器可读的 LLM_FAILURE / RUNTIME_FAILURE 区分：

1. `_fail()` 统一捕获 Exception，仅记录 `failed_step + error_message`（`"{异常类名}: {消息}"`），无错误分类字段；
2. 更关键：**多数 LLM 调用失败在底层被降级吞掉**——`parser.parse_requirement`、`tp_generator`（Phase A/B）、
   `tc_generator`、`review_soft` 均 `except Exception` 后返回空候选/中性分，Run 仍可能 COMPLETED；
3. 认证失败（不可重试）在 IR 阶段表现为 `"IR 未产出 items"` 次生症状，LLM 根因不出现在 PipelineResult；
4. LLMClient 重试耗尽的 `RuntimeError("LLM 调用失败（已重试 N 次）…")` 仅在该异常未被 orchestrator 吞掉时可见。

### §9.3 Benchmark 层分类机制（不修改 Runtime）

采用三源启发式分类器（S3/S6 实现，接口在 schema.py 冻结枚举）：

- **源 1：日志捕获**——Runner 进程内挂 logging handler，捕获已知降级点固定前缀
  （"需求解析 LLM 调用失败" / "Phase A LLM 调用失败" / "Phase B LLM 调用失败" / "LLM 用例合成调用失败" / "LLM 软维度评审调用失败"等）；
- **源 2：PipelineResult**——success / failed_step / error_message 异常类名与稳定消息前缀（"LLM 调用失败"、openai/anthropic SDK 类名）；
- **源 3：产物状态**——COMPLETED 但产物计数异常（如 TC=0、items=0 段内骤降）作降级信号。

优先级：EVALUATION_FAILURE（评价层自身异常）> INPUT_INVALID（ingest/IR 空输入类）> LLM_FAILURE（源 1/2 命中 LLM 特征）> RUNTIME_FAILURE（其余失败）> COMPLETED。

**局限（诚实记录）**：启发式存在误判面（分类规则与现有日志前缀耦合；未来 Runtime 内部重构可能改变信号）。
该机制仅用于运行可靠性归类，不影响质量指标（非 COMPLETED 不进分母），误判代价是可靠性统计口径偏差而非质量结论污染；
S7 真实运行后按实测复核，必要时**收紧分类规则只增不减**（保持 baseline 可比性）。

---

## §10 环境指纹（每次 runset 强制记录）

回答："哪个代码版本 × 哪套 Prompt × 哪个模型 × 哪个配置 × 哪个 Benchmark 数据版本"产生了这个结果。

| 字段 | 来源 |
|---|---|
| benchmark_version | BenchmarkCase / Manifest 显式字段 |
| manifest_version / manifest_id | Manifest 文件 |
| case_set | manifest.cases（哈希摘要） |
| case gold_version | 各 Case 配对的 Gold 版本 |
| git_commit | 运行时 `git rev-parse HEAD`（含脏工作区标记） |
| model_provider / model_name / temperature / enable_thinking | `GenerationConfig`（Run 已 1:1 关联） |
| prompt_version | 6 个 Prompt 版本常量聚合（复用 `scripts/v2_real_run.PROMPT_VERSIONS` 模式） |
| generator_version / reviewer_version | `GenerationConfig` |
| runner_version | eval 层自维护常量 |
| generation_config_id | `Run.generation_config_id`（ULID） |

### §10.1 敏感信息禁令（冻结）

报告 / runset 文件**禁止**出现：API Key、Authorization、base_url、任何凭证。
实现复用既有白名单模式：`CONFIG_SNAPSHOT_WHITELIST`（白名单挑字段）+ 写盘前 `assert_no_sensitive` 双保险。

### §10.2 schema_version 语义澄清（核查结论 1）

本文档与验收口径中的 "schema_version 保持 10" 指 **DB 级 `core/v2/ddl.SCHEMA_VERSION = 10`**；
不要与行级 `EntityBase.schema_version = 2`（实体序列化字段版本号）混淆。Step 11 两者都不改。

---

## §11 Baseline / Compare（第一版冻结：不做门禁）

1. 第一版只做：Baseline → Compare → Delta → 报告；
2. `--compare` 输出：delta / improvement / regression / unchanged；
3. **不定义固定阈值、不因质量下降直接 exit 1、不接 CI Gate**（核查结论 11）；
4. 第一版终点是真实产出 `baseline-v0.1.json` + 全套报告（S7）。

---

## §12 Benchmark DB 隔离（核查结论 12，纪律冻结）

1. Benchmark 必须运行于**独立 benchmark DB**（`benchmark/data/benchmark_v2.db`），严禁污染 `data/data_v2.db`；
2. 机制：`core/v2/db.set_v2_db_path()` 为进程级全局路径开关，连接建立时实时读取；
   `ensure_v2_ready()` 幂等建表自动作用于当前路径 → 零 Runtime 修改即成立；
3. **纪律**：Benchmark 只能以独立进程 CLI（`scripts/v2_benchmark.py`，S6）入口运行；
   **严禁在 Flask / 长驻进程内调用**（进程级切换会波及该进程全部请求）；测试沿用 `v2_db` fixture 的"用后恢复旧路径"模式；
4. 入库排除：`.gitignore` 现存的 `data/` 规则匹配任意层级目录，`benchmark/data/` 自动不入库；
5. Evaluator 只读 benchmark DB（Repository 只读查询面），生产 DB 不在任何评价路径上。

---

## §13 数据契约（S2 落地）

```
benchmark/
├── cases/          # *.json（BenchmarkCase）+ *_requirement.md（需求原文，S4 正式编写）
├── gold/           # *.json（BenchmarkGold）
└── manifests/      # *.json（BenchmarkManifest）

core/v2/eval/
├── __init__.py     # 导出公共符号
└── schema.py       # Pydantic 模型 + 枚举 + loader + 数据集级校验（fail-fast）
```

字段冻结（与 S2 校验要求一一对应）：

| 模型 | 字段 |
|---|---|
| BenchmarkCase | case_id, title, requirement_file, domain, difficulty, tags, context, benchmark_version, gold_ref |
| BenchmarkGold | gold_version, case_id, authoring_note, gold_authoring, gold_requirements, critical_scenarios, obligations_expected, strategy_expectations, reference_cases, acceptable_variants, forbidden_patterns |
| └ GoldRequirement | gold_id, module, type, statement, priority_hint, **match_keys**（= identity_fingerprints[] + variants[]，S3 修正 2：冻结的 ri_ 指纹是 AUTO_HIT 首选权威源，statement 重算仅作无 match_keys 时的兼容 fallback） |
| └ ObligationExpected | target, technique, description, requirement_gold_id, **min_covered_points**（S3 修正：义务至少覆盖点数，默认 1） |
| BenchmarkManifest | manifest_id, benchmark_version, cases, repeat, notes |

实现约束（冻结）：

1. 全部模型**纯 BaseModel + `extra="forbid"`，不继承 `EntityBase`**（文件级数据，非 DB 实体；身份由 case_id/gold_id/manifest_id 业务键承担）；
2. 复用 `core.schemas.common` 只读 import：`Technique / RequirementItemType / Priority`（不复制枚举造成第二套系统）；
3. 新建 eval 私有枚举：`BenchmarkRunStatus / MetricProvenance / CaseDifficulty / GoldAuthoring / StrategyFeature`；
4. 数据集级校验器（`load_benchmark_suite`，fail-fast）：case/gold 一对一配对（gold_ref 文件存在 ∧ gold.case_id==case_id）、
   duplicate case_id、manifest 引用不存在 case、`gold_authoring` 正式态（默认要求 human_independent，可显式放行）、
   manifest benchmark_version 一致性、模型内部引用完整性（scenario/strategy/variant → gold_id 存在性、feature→technique 映射合法）。

---

## §14 第一版 Benchmark Case（约 10 个，S4 正式编写）

| # | Case | domain | 考察点 |
|---|---|---|---|
| 1 | 登录/注册 | auth | 基础功能 + 字段约束（长度/格式）边界与等价类 |
| 2 | 订单退款 | order | 可参考 Step 10.5 真实运行素材（174 TP/174 TC 仅整理素材；Gold 必须人工独立确认） |
| 3 | 订单状态流转 | order | LLM 语义设计能力（状态类不伪造 Step 4 未支持的 strategy，见 §5） |
| 4 | 支付 | payment | 金额数值边界 + 异常/幂等语义 |
| 5 | 权限管理后台 | admin | permission_matrix 全覆盖主场景 |
| 6 | 搜索 | search | 结果语义准确性 + 边界（空结果/特殊字符） |
| 7 | 表单校验 | form | FieldSpec 密集：required/nullable/pattern/range 条件触发策略 |
| 8 | 文件上传 | file | 类型/大小约束 + 异常（不测真实执行，仅测设计质量） |
| 9 | 审核流程 | workflow | 跨需求项交互覆盖（Phase B 能力观察） |
| 10 | 报表导出 | report | 数据一致性语义 + 边界（空数据/大数据量描述） |

Case 业务内容必须结合 V2 实际能力编写（IR→TP→TC→Review 可走通），**不为凑类型机械构造**。

---

## §15 明确不做事项（Step 11 全程）

§2 的 18 条之外，再次显式列出：
Playwright / API 自动执行 / UI E2E 执行、Preference Learning、Prompt Optimization、
第二套 LLM Reviewer Prompt、CI 强门禁、V2 DB 新表、`core/schemas` 修改、现有 API/前端修改。
（S2 本轮额外不做：matching.py / metrics_hard.py / metrics_soft.py / report.py / benchmark runner / compare。）

---

## §16 风险与局限（诚实记录）

| 风险 | 等级 | 吸收方式 |
|---|---|---|
| Run Status 启发式分类误判（§9.3 局限） | 中 | 非 COMPLETED 不进质量分母；S7 后复核、规则只增不减 |
| Gold statement 人工复刻偏差 → AUTO_HIT 率低于预期 | 中 | §4.2 编写守则 + CANDIDATE/pending_review 兜底 + 不惩罚性判分 |
| S2 严格性未测（case_id 正则 / 必填键）导致 S4 返工 | 低 | S4 先跑 2 个真实 case 再批量 |
| LLM 非确定性导致单次结果波动 | 中 | manifest.repeat 多次运行 + 报告口径标注 |
| benchmark/ 示例与 S4 正式数据混放 | 低 | demo fixture 以 `bc_demo_` 前缀隔离，S4 交付时替换 |

---

## §17 实施顺序与验收门（冻结）

| 阶段 | 内容 | 状态 |
|---|---|---|
| **S1** | 本设计冻结文档 | 本轮 |
| **S2** | eval 包骨架 + Schema + loader 校验器 + 单测 + benchmark/ 目录与最小 fixture | 本轮 |
| S3 | 确定性 Evaluator：matching.py（四态）+ metrics_hard.py（CODE provenance 指标） | 待验收后 |
| S4 | 10 个正式 Case + 人工独立 Gold | 待 |
| S5 | 语义消费层：metrics_soft（LLM 语义指标）+ pending_review 清单 | 待 |
| S6 | scripts/v2_benchmark.py Runner（独立 DB + 日志捕获分类器 + 环境指纹 + runset 快照） | 待 |
| S7 | 真实跑 10 case × repeat → **baseline-v0.1.json** + 全套报告 | 待 |
| S8 | compare + delta 报告（无门禁） | 待 |

每阶段验收门：单测绿 + 全量 pytest 绿 + ruff 绿 + git diff 不越冻结边界。

---

## 附录 A：实施前核查 12 条结论 → 本文档落点

| # | 核查结论 | 落点 |
|---|---|---|
| 1 | schema_version 指 `ddl.SCHEMA_VERSION=10`，勿与 `EntityBase.schema_version=2` 混淆 | §10.2 |
| 2 | `BenchmarkRunStatus` eval 私有五态枚举 | §9.1、schema.py |
| 3 | `MetricProvenance` eval 私有枚举 CODE/LLM/MIXED | §7、schema.py |
| 4 | gold_authoring 正式态 = human_independent；系统输出仅为 reference material | §3 |
| 5 | 四态 Matching；similarity 仅候选/诊断 | §4 |
| 6 | TP 数量不设统一门槛，以 obligation / critical_scenario 完成度为核心 | §3、§6 |
| 7 | Strategy expectation 条件触发（对齐 Step 4 实际能力） | §5 |
| 8 | GOLD-BASED 与 REVIEWER-BASED 双轨分离 | §7 |
| 9 | 技术运行失败与质量失败分离 | §9 |
| 10 | 不改 Runtime；Benchmark 层日志捕获 + PipelineResult + 产物状态启发式分类（含局限记录） | §9.2、§9.3 |
| 11 | Compare 第一版不做 CI 强门禁 | §11 |
| 12 | 独立 benchmark DB，严禁污染 data/data_v2.db | §12 |
