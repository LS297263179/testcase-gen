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

---

## 附录 B：S6 Benchmark Runner 实施说明（2026-09-21 验收通过）

> 性质：本附录是 §9.3 / §10 / §12 / §13 在 S6 落地时的**实施细化记录**，不改变 §2~§15 的任何冻结结论。
> 全部规则已由 `tests/test_benchmark_runner.py`（58 例，零真实 LLM）锁定。
> 交付：`core/v2/eval/runner_lib.py`（纯函数层）+ `scripts/v2_benchmark.py`（独立 CLI 进程）+ `.gitignore` 一行。

### B.1 五态分类补充：源 3 产物计数采用「阶段门控」

§9.3 源 3 若照字面直接判零，会把"尚未执行的阶段自然产生零计数"误报为 LLM_FAILURE
（例：Runtime 在 testpoints 阶段因非 LLM 原因失败，下游 `test_cases` 天然为 0，却被判 LLM 故障）。
S6 实现按"该阶段是否真的跑到"门控：

| 降级信号 | 生效前提（缺一不计） | 规则 id |
|---|---|---|
| `items == 0` | IR 阶段已实际执行（`steps` 中存在 `ir` 记录，含 "IR 未产出 items" 失败点） | `count_items_zero` |
| `test_cases == 0` | TestCase 阶段已执行且**成功**，且无非 LLM 失败原因可解释该零产出 | `count_test_cases_zero` |
| `reviewed == 0` | Review 阶段本应执行 + 已有 TestCase + Review 结果明确缺失/失败 | `review_result_missing` |

> 这是对 §9.3 三源启发式分类的实施细化，用于避免“尚未执行的阶段自然零计数”误报为 LLM_FAILURE。

门控方向仍保留 §9.2-3 的取证口径：**IR 跑完却 0 items 仍判 LLM_FAILURE**——认证类 LLM 故障被底层吞掉后，
这是 PipelineResult 上唯一可机器读取的症状。残余误判代价照旧由"非 COMPLETED 不进质量分母"吸收（§9.1、§16）。

同期锁定的其他分类细则：
- 源 1 只匹配 §9.3 列举的 **5 条最终降级**日志前缀；`LLM 调用失败（第 N 次）…后重试` 与
  `模型不支持图片输入，已自动忽略图片` 计入 `ignored_intermediate_signals`，不参与判 LLM_FAILURE。
- 日志按 (case, repeat) 单元的 `mark/since` **增量窗口**归属，跨 case、跨 repeat 互不串信号。
- 原始日志与异常文本**不整段落盘**：只留 `safe_prefix(≤160 字符) + hash(16 位)` 与命中的规则 id。

### B.2 S6 已冻结实现决策

| # | 决策 | 冻结依据 |
|---|---|---|
| 1 | `success=True` + 已确认 LLM 降级信号 → `LLM_FAILURE`（核心产物已降级，不进质量分母） | §9.1、§9.3 |
| 2 | 非 COMPLETED：仍运行 S3 Hard Eval（保留 cost/counts 观测，质量格按 S3 契约自动为 None），**不调用 S5 真实 LLM** | §6 指标 #10、§9 |
| 3 | ARCHIVED TestCase 不进入最终质量评价集合，仅以 `archived_test_case_ids` / `archived_test_cases` 作运行与优化观察 | §3.5、Step 8 语义 |
| 4 | Benchmark DB 默认 **fresh**（runset 前清理库文件与 WAL 伴随文件）；`--reuse-db` 仅供调试；两者互斥 | §12 |
| 5 | Runner 默认执行 S5，**不提供 `--skip-soft`**（正式 runset 必须含语义轨道） | §1 可重复定位 |
| 6 | S6 输出目录 `benchmark/runsets/<runset_id>/`：`runset.json` + `cases/<case_id>__rNN.json` + `manifest.resolved.json` + `_COMPLETE.json` | §0 目标链路 |
| 7 | `benchmark/runsets/` **不进入 Git**（baseline 由 S7 显式提升入库） | §12.4 同族纪律 |
| 8 | `HardMetricsReport` 序列化由 S6 `runner_lib.hard_to_payload/from_payload` 提供，`metrics_hard.py` 零改动 | §2 边界 3、11 |
| 9 | S5 客户端经现有 `build_llm_client("review")` 获取（review 未启用时工厂自带回退到 generate 配置），不改 `client_factory.py`、不新增 purpose | §2 边界 2、3 |
| 10 | Runtime 一律 `client=None` 调用（保持产品路径 generate/review 双客户端行为不变）；S5 的 LLM 调用数单独对账为 `llm_calls{pipeline, s5_soft, total}` | §2 边界 2、§6 指标 #10 |

### B.3 S6 环境指纹与落盘安全实施

- 指纹覆盖 §10 表格全项（23 字段），并额外并列 `business_prompt_versions`（6 个常量）与
  `s5_prompt_version`：`GenerationConfig.prompt_version` 由 Step 3/5 逐步追加、`reviewer_version` 由 Step 7 追加，
  **单靠 GenerationConfig 不足以代表 6 个业务 Prompt**，故 runset 同时记常量聚合与 cfg 原值。
- `case_set_digest` = manifest 所列 case 的 `case.json + requirement.md + gold.json` 字节摘要聚合哈希；
  另有 per-case `case_fingerprint`（`gold_version` + 三个文件摘要），供 S8 判定数据集是否发生过漂移。
- `git_probe`：git 不可用或调用失败 → `git_commit/git_branch/git_dirty = null` + `git_probe="unknown"`，**不阻断 benchmark**。
- 敏感防护沿用 §10.1 双保险（白名单挑字段 + 写盘前 `assert_no_sensitive`），关键字在既有基础上追加
  `base_url` / `credential`；`resolved_benchmark_db` 只记路径，不含任何凭证。
- 半成品保护：runset 目录先落 `INCOMPLETE` 标记，全部落盘成功后才写 `_COMPLETE.json` 并摘除标记；
  敏感自检或写盘失败 → exit 5 且不产 `runset.json`（避免留下"看起来完整"的 runset）。
- CLI 退出码：`0=全 COMPLETED / 1=存在非 COMPLETED / 2=参数错误 / 3=DB 与生产库路径保护 / 4=数据集校验失败 / 5=落盘或敏感自检失败`。

---

## 附录 C：S7 三轨关联口径（架构裁决 D1/D2/D3/D9/D10，2026-09-22）

> 本附录只**追加**，不修改 §2~§17 与附录 A/B 的冻结正文。凡与 B.2 第 8 条（"`metrics_hard.py` 零改动"）
> 冲突之处，以本附录为准：该条是 S6 期的实施决策，S7 经裁决 D2 明确授权修改 `metrics_hard.py` 与 `runner_lib.py`。

### C.1 AUTO_HIT 的正式重定义

S7 起 `AUTO_HIT` 定义为 **"确定性关联命中"**，**不再等同 fingerprint exact**。每条 `AUTO_HIT` 必须携带
`via ∈ {identity, bridge, anchor}`；`CANDIDATE / AMBIGUOUS / MISS` 一律不自动计分，照旧进 `pending_review`。

报告必须**同时**展示三项，缺一不可：`requirement_coverage`、`identity_match_rate`、`via_counts`。

> **禁止将 `AUTO_HIT=0` 或 `identity_match_rate=0` 解读为 requirement coverage = 0。**
> 该禁令已机器化：`IdentityDiagnostics.to_dict()["note"]` 与 `identity_match_rate` 的 `MetricCell.detail`
> 均自带此句，测试 `TestStepBReportSurface::test_identity_zero_does_not_imply_coverage_zero` 强制校验。

§4.1 的四态匹配规则、`CANDIDATE_SIMILARITY_FLOOR`、`ri_` 指纹公式、module 匹配、相似度实现**一字未改**；
`matching.py` 本轮的全部 diff 仅为裁决 D8 的机械抽取（import 替换 + 删 `_anchor_satisfied` 定义 + 2 处调用改名）。

### C.2 三轨定义与优先级

| 轨 | 通道 | 定位 | 计分 |
|---|---|---|---|
| **Identity** | `ri_` 指纹精确相等（`matching.match_gold_requirements` 原样复用） | 诊断 parser 身份稳定性 | 计入 `identity_match_rate`，**不单独代表覆盖** |
| **Semantic · bridge** | `Gold.obligations_expected.requirement_gold_id` → runtime `CoverageObligation` 的 `(technique, target)` 精确匹配 → `obligation.item_id` | 核心：全链路结构化、零相似度、跨模型稳定 | 计入 `requirement_coverage` |
| **Semantic · anchor** | `Gold.critical_scenarios` 的 `expected_actions/expected_outcomes` 在结构化字段内子串判定 → 经 `requirement_gold_ids` 形成 Gold 映射 | 核心：覆盖无 obligation 桥的需求 | 计入 `requirement_coverage` |
| **Structural** | `structural_validity` / `duplication_score`（`review_hard` 口径） | 不变 | 不变 |

关联优先级与去重：**`identity > bridge > anchor`**，同一 Gold 只计一次。`obligation_coverage`、
`structural_validity`、`duplication_score`、`reviewer_based` 四项口径**完全不变**。

anchor 轨的准入条件（裁决 D3，六条全部强制）：① 必须对应 Gold `critical_scenario`；② 只检查
`step.action` / `tc.expected`；③ **不扫描** `title` / `precondition` / `remark` 等自由文本；④ **不使用**
similarity/fuzzy 自动计分（相似度只出现在 Identity 诊断里，用于挑"最像的那条"作对照）；⑤ 必须有明确的
`requirement_gold_id`；⑥ candidate/pending 永不自动计分。

### C.3 anchor 作用域（裁决 D9）

anchor 判定的 haystack = **全量非 ARCHIVED TestCase**（`HardMetricsReport.anchor_scope =
"all_non_archived_testcases"`，随 payload 落盘），而非"仅追溯到已 identity 命中 item 的 TC"。
理由：否则等于把 identity 前置依赖重新引入 anchor 轨，三轨退化为单轨。ARCHIVED 由 S6 装配层
（`runner_lib.load_eval_artifacts`）排除，与附录 B.2 第 3 条同源。

场景判定不再以 identity AUTO_HIT 作为**准入门**：引用项未 identity 命中不再直接判 MISSING/AMBIGUOUS，
而是照常按结构化锚点判定；identity 是否命中降级为 `anchors["requirement_items"]` **诊断位**。
未声明任何结构化锚点的场景 → `AMBIGUOUS` + `scenario_unjudgeable` pending，不强行计分。

### C.4 D10 不猜原则

anchor 命中多个 runtime item 时：`requirement_coverage` **计入**，但 `item_id = None`（不猜）、保留
`item_ids`、登记 `anchor_item_ambiguous` pending、**不向 S5 提供 trace**。`metrics_soft.py` 零改动即满足此约束
——它只鸭子类型读取 `state == AUTO_HIT and m.item_id`，`item_id=None` 的项自然落入 `undetermined_items`。
反查不到任何 item 时登记 `anchor_item_unresolved`。

`RailMatch.matches.auto` 只收"唯一 item 映射"，因此 `requirement_precision` 与 S5 trace 永不基于猜测。
`strategy_outcomes` 对 `auto` 中缺失的 Gold 返回 `met=None`（不可判），**不伪造 False**。

### C.5 双模型对照证据（回归对照基线，非质量目标、非能力排名）

bc_01_login 同条件 smoke 各 1 次，gold-v0.2 口径，经 `evaluate_hard_metrics` 报告层复算：

| 观测项 | deepseek smoke | qwen smoke |
|---|---|---|
| `requirement_coverage` | 8/10 | 8/10 |
| `identity_match_rate` | 0.0 | 0.0 |
| `via_counts` | identity 0 / bridge 5 / anchor 3（+candidate 2） | identity 0 / bridge 5 / anchor 3（+miss 1 / candidate 1） |
| `critical_scenario_coverage` | 3/6 | 3/6 |
| statement 逐字率 | 0/10 | 0/10 |
| module 一致数 | 3/10 | 0/10 |
| type 一致数 | 9/10 | 8/10 |
| 最佳相似度均值 | 0.5994 | 0.4523 |
| AUTO_HIT 中有唯一 item_id | 7 / 8 | 7 / 8 |
| S5 送评 TC | 43 / 66 | 42 / 48 |

**这些数字只是本次重构的 regression acceptance fixture**，不得表述为产品质量目标、验收门槛或 AI 能力评分；
两列差异只陈述可观测事实（module 重命名程度、措辞相似度），**不构成模型能力优劣排名**。
裁决 D5：`qwen3.8-flash` 为 v0.1 正式 baseline 模型，DeepSeek 仅历史/对照 smoke，两者指标不得混算。

关键归因结论：`identity_match_rate = 0` 的根因是 Step 2 parser 对 statement 的改写（两模型逐字率均为 0/10），
**且已有 3 条 Gold 项本身就是需求原文逐字仍不命中** → 无论改写 Gold 措辞还是强制 parser 逐字照抄都无法修复。
这正是引入 bridge/anchor 两轨的依据，也是裁决 4"不得为了 benchmark 把 Parser 变成复制器"的实证基础。

### C.6 与 S3 的行为差异清单（值或语义变化，均已核对既有断言）

1. `requirement_matches` 元素类型 `ItemMatch` → `RailMatch`（子类，多 `via`），既有读取路径不受影响。
2. `scenario_outcomes[].anchors["requirement_items"]` 语义：由"引用项 identity AUTO_HIT"变为"引用项 **via=identity** 关联"；
   bridge 关联的 Gold 该诊断位为 False。仅诊断，不参与计分。
3. `requirement_coverage.detail` / `scenario.detail` 文案变更（新增 `via=` 分布）。
4. `missing_risk["strategy_unmet_ids"]` **含义**由"多数不可判（met=None）"变为真实 True/False。
5. `missing_risk["gold_miss_ids"]` 为三轨后状态：被 bridge/anchor 救回的 Gold 不再算 MISS；
   identity 侧的缺失改由 `identity_diagnostics.identity_miss_gold_ids` 单独承载，**不计入 `total_open`**（避免诊断污染风险计数）。
6. `requirement_precision` / `test_point_precision` 分子 `consumed_item_ids` 现含 bridge 解析出的 item，定义变宽。
7. `pending_review` 新增 3 个 kind：`anchor_item_ambiguous` / `anchor_item_unresolved` / `scenario_unjudgeable`。
8. payload 形状：`metrics` 7 → **8** cell（新增 `identity_match_rate`）；`requirement_matches[]` 新增 `via`；
   顶层新增 `via_counts` / `identity_diagnostics` / `anchor_scope`。
   `hard_from_payload` 一律用 `.get()` 读取新键 → **S6 形态 runset 仍可直接加载**（已用两个磁盘上的真实
   runset 验证：`via==""`、`via_counts=={}`、`identity_match_rate is None`，再序列化对既有字段无损）。

既有断言变化：**共 0 条**。唯一受影响的 `test_benchmark_bc02_synthetic.py::test_B_missing_item_breaks_auto_hit`
通过给 `_ideal_runtime` 增加 2 行（`omit_gr` 时连带跳过引用它的场景）恢复其原本文档声明的负例语义
——"删掉一条 runtime item → 对应 Gold 不再 AUTO_HIT、Coverage 下降、关联场景非 COVERED"。
原 fixture 只删 `RequirementItem` 却保留了携带锚点文本的 TC，在三轨下不再是"需求缺失"负例，
而变成"identity 指纹缺失但结构化证据存在"（该情形已由 `tests/test_benchmark_rails.py` 在合成与两个真实库上覆盖）。
`omit_gr` 为 None 时新增分支恒不触发，A/C/D 与 10 个 formal case 全绿契约经实测不受影响。

### C.7 挂账（本轮明确不做）

| 项 | 状态 | 说明 |
|---|---|---|
| **D7①** | 仅立案 | `POST /api/model-config` 缺空值守卫，可写入空 model/base_url。**本轮不修改**（裁决 9） |
| **D7②** | 已实施 | `core/config.py:get_model_config` 增 4 行空配置守卫（非 dict 或 generate/review 两段皆空 → 落 yaml 兜底并留 warning）；独立可回滚，配 `TestModelConfigEmptyGuard` 5 例；与 S7 三轨逻辑零耦合 |
| **D13** | 挂 Gold v0.3 | bc_01 §5.3 前后端双拦截覆盖、「验证码错误」场景暂不补，避免混淆归因 |
| **bc_03 缺 obligations** | 挂 Gold v0.3 | `bc_03_order_status` 的 `obligations_expected` 为空 → bridge 轨对该 case 不可用，真实运行时其 `requirement_coverage` 只能靠 identity+anchor；`benchmark/gold` 属只读冻结面，本轮授权仅覆盖 bc_01 anchor |
| **bc_demo_login 指纹** | 待修 | 2 条 `ri_` 指纹（`gr-login-ok` / `gr-phone-len`）与重算值不符；因 `test_benchmark_formal_data.py` 只过滤 `bench-v0.1` 而从未触发 |
| **D14** | 立案未实施 | 多 Gold 场景（N>1）反查到 M 个 item 且 `M != N` 时，应同样按 D10 处理（`item_id=None` + pending），因单个 item 无法被确定性认定为 N 条 Gold 的共同唯一表达。前置已实测：10 个 formal case 理想 runtime 下 3 个多 Gold 场景全部 M==N，触发 0 次 → formal 全绿契约不受影响；bc_01 无多 Gold 场景 → C.5 全部数字不受影响。作用域仅限真实运行中 identity 与 bridge 双双落空的情形 |

### C.8 S7 代码落点

- 新增 `core/v2/eval/textops.py`（44 行，裁决 D8）：仅 `anchor_satisfied` / `statement_similarity` 两个通用 helper，
  不扩展为额外 NLP 层；防漂移测试 `test_no_drift_against_change_impact_similarity` 锁定其与
  `change_impact._similarity` 同口径。
- 新增 `core/v2/eval/rails.py`（裁决 D2）：`associate_gold` 为 `metrics_hard` 的唯一关联入口，
  产出 `RailReport{matches, scenario_outcomes, strategy_outcomes, diagnostics, associated_gold_ids, via_counts, pending, anchor_scope}`。
  不 import `runtime` / `metrics_hard` / `metrics_soft`（避免循环依赖，已由 AST 边界测试强制）。
- 改 `core/v2/eval/metrics_hard.py`：`HardMetricsReport` 新增 `identity_match_rate` / `via_counts` /
  `identity_diagnostics` / `anchor_scope`；非 COMPLETED 跳过清单同步纳入 `identity_match_rate`。
- 改 `core/v2/eval/runner_lib.py`：`_HARD_CELLS` 7 → 8，payload 增 `via` / `via_counts` / `identity_diagnostics` / `anchor_scope`，
  `hard_from_payload` 向后兼容。
- **未改**：`matching.py`（仅 D8 机械抽取）、`metrics_soft.py`、`schema.py`、Runtime、Parser、6 个业务 Prompt、
  DDL、`schema_version`（仍为 10）、V2 API、Frontend、`scripts/v2_benchmark.py`。
