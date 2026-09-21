"""V2 Step 11 Benchmark 数据结构 Schema（S2）- Case / Gold / Manifest + loader + 数据集级校验。

设计基线：docs/v2/step11-benchmark-evaluation.md §3~§5、§9、§13（v1.0 冻结）。
定位：Benchmark 是 Runtime 外部的独立观测层，本模块定义其三类文件级数据契约：
  - BenchmarkCase     benchmark/cases/*.json      一个被测业务场景（需求原文 + 元信息）
  - BenchmarkGold     benchmark/gold/*.json       人工独立分析产出的最低必要标准（floor）
  - BenchmarkManifest benchmark/manifests/*.json  一次 runset 的 case 清单与重复次数

冻结要点（核查结论落码）：
  - 全部模型纯 BaseModel + extra="forbid"，**不继承 EntityBase**（文件级数据非 DB 实体，
    身份由 case_id / gold_id / manifest_id 业务键承担，不引入 ULID/时间戳）。
  - BenchmarkRunStatus（五态）与 MetricProvenance（CODE/LLM/MIXED）为 **eval 私有枚举**，
    与 core.schemas.common.RunStatus / Provenance 互不引用、互不改写。
  - Technique / RequirementItemType / Priority 只读复用 core.schemas（不复制第二套枚举）。
  - StrategyFeature 与 Step 4 策略引擎实际能力一一对应，feature→technique 映射由校验器强制。
  - Gold 独立性机检：gold_authoring 正式态必须为 human_independent（loader 默认严格）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.schemas.common import Priority, RequirementItemType, Technique

# 业务键格式：小写字母/数字开头，允许小写字母/数字/下划线/连字符，总长 2~64
_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,63}$"
# ri_ identity fingerprint 格式（compute_item_identity_fingerprint 输出）
_RI_FINGERPRINT_RE = re.compile(r"^ri_[0-9a-f]{32}$")


# ============================================================
# eval 私有枚举
# ============================================================


class BenchmarkRunStatus(StrEnum):
    """单 case 运行状态五态（设计文档 §9.1；eval 私有，勿与 common.RunStatus 混用）。

    非 COMPLETED 的 case 不进质量指标分母（质量与运行可靠性分离，冻结）。
    分类信号来自 Benchmark 层启发式（日志捕获 + PipelineResult + 产物状态），
    不修改 Runtime —— 局限与优先级见设计文档 §9.3。
    """

    COMPLETED = "completed"
    LLM_FAILURE = "llm_failure"
    RUNTIME_FAILURE = "runtime_failure"
    EVALUATION_FAILURE = "evaluation_failure"
    INPUT_INVALID = "input_invalid"


class MetricProvenance(StrEnum):
    """指标结果来源三态（设计文档 §7；eval 私有，与 common.Provenance 是两套枚举）。

    CODE=确定性代码计算 / LLM=语义判断产物 / MIXED=两者混合（报告必须标注）。
    """

    CODE = "code"
    LLM = "llm"
    MIXED = "mixed"


class CaseDifficulty(StrEnum):
    """Case 难度（设计文档 §14：结合 V2 实际能力分级，影响报告分组观察，不影响计分）。"""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class GoldAuthoring(StrEnum):
    """Gold 产出方式（设计文档 §3 独立性声明的机检字段）。

    - HUMAN_INDEPENDENT：人工独立分析原始需求产出（正式态，唯一可进入正式评价）。
    - HUMAN_ASSISTED：参考系统输出整理后人工逐条确认（过渡态；loader 默认拒绝，
      仅素材整理时显式放行）。禁止的是"生成→筛选→直接作为 Gold"闭环。
    """

    HUMAN_INDEPENDENT = "human_independent"
    HUMAN_ASSISTED = "human_assisted"


class StrategyFeature(StrEnum):
    """Strategy 条件触发特征（设计文档 §5）：只定义 Step 4 策略引擎实际消费的能力。

    需求具备什么测试特征，就要求什么策略；DECISION_TABLE / STATE_TRANSITION /
    ERROR_GUESSING / SCENARIO 为 Step 4 明确延后能力，不得进入 strategy_expectations
    （状态流转类考察 LLM 语义设计，由 critical_scenarios 承担）。
    """

    NUMERIC_RANGE = "numeric_range"  # FieldSpec.min/max_value → 六点边界
    LENGTH_RANGE = "length_range"  # FieldSpec.min/max_length → 长度边界
    ENUM_FIELD = "enum_field"  # enum_values → 合法/非法等价类
    PATTERN_FIELD = "pattern_field"  # FieldSpec.pattern
    REQUIRED_FIELD = "required_field"  # required 提供/缺失两类
    NULLABLE_FIELD = "nullable_field"  # nullable 语义
    UNIQUE_FIELD = "unique_field"  # unique 约束
    TYPED_FORMAT_FIELD = "typed_format_field"  # data_type ∈ {email, phone, url, id_card}
    PERMISSION_RULE = "permission_rule"  # role × resource × action 矩阵


# feature → technique 合法映射（与 core/v2/strategy 三策略实际实现一致，校验器强制）
STRATEGY_FEATURE_TECHNIQUES: dict[StrategyFeature, Technique] = {
    StrategyFeature.NUMERIC_RANGE: Technique.BOUNDARY_VALUE,
    StrategyFeature.LENGTH_RANGE: Technique.BOUNDARY_VALUE,
    StrategyFeature.ENUM_FIELD: Technique.EQUIVALENCE_CLASS,
    StrategyFeature.PATTERN_FIELD: Technique.EQUIVALENCE_CLASS,
    StrategyFeature.REQUIRED_FIELD: Technique.EQUIVALENCE_CLASS,
    StrategyFeature.NULLABLE_FIELD: Technique.EQUIVALENCE_CLASS,
    StrategyFeature.UNIQUE_FIELD: Technique.EQUIVALENCE_CLASS,
    StrategyFeature.TYPED_FORMAT_FIELD: Technique.EQUIVALENCE_CLASS,
    StrategyFeature.PERMISSION_RULE: Technique.PERMISSION_MATRIX,
}


# ============================================================
# BenchmarkCase
# ============================================================


class BenchmarkCase(BaseModel):
    """一个被测业务场景：需求原文文件 + 元信息，1:1 配对一份 Gold。

    字段口径（设计文档 §13）：requirement_file 相对 benchmark/cases/ 目录；
    gold_ref 为 Gold 文件名（相对 benchmark/gold/，配对锚点，与 gold.case_id 双向校验）。
    """

    case_id: str = Field(pattern=_ID_PATTERN)
    title: str
    requirement_file: str  # 需求原文（.md/.txt），相对 benchmark/cases/
    domain: str  # auth / order / payment / ...（报告分组维度）
    difficulty: CaseDifficulty
    tags: list[str] = Field(default_factory=list)
    context: str = ""  # 背景补充（测试范围/角色约定等），供 Gold 编写与报告解读
    benchmark_version: str  # 本 case 所属 Benchmark 数据版本
    gold_ref: str  # 配对的 Gold 文件名（如 "bc_demo_login.gold.json"）

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_gold_ref_name(self) -> BenchmarkCase:
        """gold_ref 必须是 .json 文件名（不含路径分隔符；存在性由 loader 配对校验）"""
        if not self.gold_ref.endswith(".json") or "/" in self.gold_ref or "\\" in self.gold_ref:
            raise ValueError(f"case {self.case_id}: gold_ref 必须是 benchmark/gold/ 下的 .json 文件名")
        return self


# ============================================================
# BenchmarkGold（嵌套子模型）
# ============================================================


class GoldMatchKeys(BaseModel):
    """Gold 冻结匹配键（S3 修正 2）：人工评审时固化的 identity fingerprints 是 AUTO_HIT 的**首选权威源**。

    - identity_fingerprints：ri_ 指纹（Gold 视角冻结，即使 Runtime statement 微调也能精确命中）；
    - variants：可接受的 statement 变体（仅在无显式 fingerprint 命中意义下作为辅助 recomputed 候选）。
    有 match_keys 时不再用 statement 重算覆盖（重算仅作无 match_keys 时的兼容 fallback，见 matching.py）。
    """

    identity_fingerprints: list[str] = Field(min_length=1)
    variants: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @field_validator("identity_fingerprints")
    @classmethod
    def _check_ri_format(cls, v: list[str]) -> list[str]:
        """指纹必须是 ri_ + 32 位十六进制（compute_item_identity_fingerprint 输出格式）"""
        bad = [f for f in v if not _RI_FINGERPRINT_RE.fullmatch(f)]
        if bad:
            raise ValueError(f"identity_fingerprints 非法（应为 ri_ + 32hex）: {bad}")
        return v


class GoldRequirement(BaseModel):
    """Gold 需求项：人工独立分析原始需求得到的最低必要覆盖集合（floor，非全集）。

    匹配协议（设计文档 §4.1）：优先用 match_keys.identity_fingerprints 与 run 内
    RequirementItem 指纹精确配对 → AUTO_HIT；无 match_keys 时才用
    compute_item_identity_fingerprint(module, type, statement) 重算作兼容 fallback；
    两者都未命中才进入 similarity → CANDIDATE（不计分）。
    """

    gold_id: str = Field(pattern=_ID_PATTERN)
    module: str
    type: RequirementItemType
    statement: str
    priority_hint: Priority | None = None
    match_keys: GoldMatchKeys | None = None  # 缺省时 matching 层用 statement 重算（fallback）

    model_config = ConfigDict(extra="forbid")


class CriticalScenario(BaseModel):
    """关键场景（指标 #1）：结构化 anchor 判定，禁止仅靠关键词命中（设计文档 §8）。

    核心结构锚点：requirement_gold_ids（对应 Gold 需求项）
    + expected_techniques（对应 technique/obligation）
    + expected_actions / expected_outcomes（对应动作或 expected 结果）。
    任一锚点无法自动可靠判断 → AMBIGUOUS → 人工复核，不强行计分。
    """

    scenario_id: str = Field(pattern=_ID_PATTERN)
    title: str
    requirement_gold_ids: list[str] = Field(min_length=1)  # → gold_requirements.gold_id
    expected_techniques: list[Technique] = Field(default_factory=list)
    expected_actions: list[str] = Field(default_factory=list)
    expected_outcomes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class ObligationExpected(BaseModel):
    """期望义务：按 target × technique 声明必须被覆盖的结构性义务点（指标 #3 判分依据）。

    min_covered_points（S3 修正）：该义务至少需被多少个 TestPoint 覆盖（如边界义务需 min/minus/max/plus
    多点）；覆盖数不足 → UNDER_COVERED，义务不存在 → MISSING。
    """

    target: str  # 与 CoverageObligation.target 同口径：字段名 / 字段名.length / 角色-资源
    technique: Technique
    description: str
    requirement_gold_id: str | None = None  # 可选回链 Gold 需求项
    min_covered_points: int = Field(default=1, ge=1)  # 至少覆盖点数（默认 1）

    model_config = ConfigDict(extra="forbid")


class StrategyExpectation(BaseModel):
    """条件触发策略要求（设计文档 §5）：需求具备 feature 就必须出现对应 technique。"""

    feature: StrategyFeature
    technique: Technique
    requirement_gold_id: str  # → gold_requirements.gold_id（触发特征所在需求项）

    model_config = ConfigDict(extra="forbid")


class GoldReference(BaseModel):
    """参考素材留痕：现有 V2 输出（如 Step 10.5 真实运行产物）只能作为整理素材，不是 Gold 来源。"""

    source: str  # 如 "v2_run_01M2QBWABYW3VTNYK5BBX2WDN4"（run_id 为随机标识，审计保留）
    note: str = ""

    model_config = ConfigDict(extra="forbid")


class AcceptableVariant(BaseModel):
    """可接受变体：Gold 项在表述/拆分粒度上的合理差异（AI 合理换写法时不判错）。"""

    for_gold_id: str  # → gold_requirements.gold_id
    description: str

    model_config = ConfigDict(extra="forbid")


class ForbiddenPattern(BaseModel):
    """禁止模式：产物中出现即 INVALID（代码可明确判错，设计文档 §6.1 三态之一）。"""

    pattern_id: str = Field(pattern=_ID_PATTERN)
    description: str
    severity_note: str = ""  # 判错依据说明（保证 INVALID 可解释）

    model_config = ConfigDict(extra="forbid")


class BenchmarkGold(BaseModel):
    """Gold 标准：一个 Case 的人工独立最低必要标准（1:1 配对，键为 case_id）。

    除 case_id/gold_version 外均为"必答题"（键必须出现，可为空列表 = 显式声明无此项）。
    内部引用完整性由 model_validator 强制（gold_id 唯一 + 所有回链存在 + feature→technique 合法）。
    """

    gold_version: str
    case_id: str = Field(pattern=_ID_PATTERN)
    authoring_note: str  # 编写过程记录（人工独立分析方法/参考了哪些素材），可为空串但必须显式
    gold_authoring: GoldAuthoring
    gold_requirements: list[GoldRequirement] = Field(min_length=1)
    critical_scenarios: list[CriticalScenario]
    obligations_expected: list[ObligationExpected]
    strategy_expectations: list[StrategyExpectation]
    reference_cases: list[GoldReference]
    acceptable_variants: list[AcceptableVariant]
    forbidden_patterns: list[ForbiddenPattern]

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_internal_refs(self) -> BenchmarkGold:
        """Gold 内部引用完整性：业务键唯一 + 所有回链 gold_id 存在 + 策略映射合法。"""
        errors: list[str] = []

        gold_ids = [g.gold_id for g in self.gold_requirements]
        if len(gold_ids) != len(set(gold_ids)):
            errors.append(f"gold_requirements.gold_id 重复: {sorted(gold_ids)}")
        id_set = set(gold_ids)

        for label, ids in [
            ("scenario_id", [s.scenario_id for s in self.critical_scenarios]),
            ("pattern_id", [p.pattern_id for p in self.forbidden_patterns]),
        ]:
            if len(ids) != len(set(ids)):
                errors.append(f"{label} 重复: {sorted(ids)}")

        for sc in self.critical_scenarios:
            missing = [gid for gid in sc.requirement_gold_ids if gid not in id_set]
            if missing:
                errors.append(f"critical_scenario {sc.scenario_id} 引用不存在的 gold_id: {missing}")

        for st in self.strategy_expectations:
            if st.requirement_gold_id not in id_set:
                errors.append(
                    f"strategy_expectation({st.feature.value}) 引用不存在的 gold_id: {st.requirement_gold_id}"
                )
            expected = STRATEGY_FEATURE_TECHNIQUES[st.feature]
            if st.technique != expected:
                errors.append(
                    f"strategy_expectation({st.feature.value}) 的 technique 必须为 {expected.value}（Step 4 能力映射冻结）"
                )

        for ob in self.obligations_expected:
            if ob.requirement_gold_id is not None and ob.requirement_gold_id not in id_set:
                errors.append(f"obligation({ob.target}) 引用不存在的 gold_id: {ob.requirement_gold_id}")

        for av in self.acceptable_variants:
            if av.for_gold_id not in id_set:
                errors.append(f"acceptable_variant 引用不存在的 gold_id: {av.for_gold_id}")

        if errors:
            raise ValueError("; ".join(errors))
        return self


# ============================================================
# BenchmarkManifest
# ============================================================


class BenchmarkManifest(BaseModel):
    """runset 清单：本次 Benchmark 跑哪些 case、每个重复几次（设计文档 §13）。"""

    manifest_id: str = Field(pattern=_ID_PATTERN)
    benchmark_version: str
    cases: list[str] = Field(min_length=1)  # case_id 列表（存在性由 loader 校验）
    repeat: int = Field(default=1, ge=1)  # 每 case 重复运行次数（吸收 LLM 非确定性）
    notes: str = ""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_unique_cases(self) -> BenchmarkManifest:
        """cases 不得重复（重复运行次数由 repeat 表达）"""
        if len(self.cases) != len(set(self.cases)):
            raise ValueError(f"manifest {self.manifest_id}: cases 存在重复项: {sorted(self.cases)}")
        return self


# ============================================================
# 数据集级 loader + 校验（fail-fast）
# ============================================================


class BenchmarkValidationError(ValueError):
    """Benchmark 数据集级校验失败（配对/引用/版本/正式态；区别于单模型 ValidationError）。"""


@dataclass
class BenchmarkSuite:
    """一次加载并校验通过的完整 Benchmark 数据集（case_id 为主键的三索引）。"""

    root: Path
    cases: dict[str, BenchmarkCase] = field(default_factory=dict)
    golds: dict[str, BenchmarkGold] = field(default_factory=dict)  # key = case_id
    manifests: dict[str, BenchmarkManifest] = field(default_factory=dict)  # key = manifest_id


def _load_json(path: Path) -> Any:
    """读取 JSON 文件（utf-8）；解析失败包装为 BenchmarkValidationError（带文件上下文）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BenchmarkValidationError(f"{path.name}: JSON 解析失败: {exc}") from exc


def load_case(path: Path) -> BenchmarkCase:
    """加载单个 BenchmarkCase（单模型校验：缺字段/类型错/多余字段 → pydantic ValidationError）"""
    return BenchmarkCase.model_validate(_load_json(path))


def load_gold(path: Path) -> BenchmarkGold:
    """加载单个 BenchmarkGold（含内部引用完整性校验）"""
    return BenchmarkGold.model_validate(_load_json(path))


def load_manifest(path: Path) -> BenchmarkManifest:
    """加载单个 BenchmarkManifest"""
    return BenchmarkManifest.model_validate(_load_json(path))


def _load_dir(directory: Path, loader):
    """加载目录下全部 *.json（按文件名排序保证确定性）；返回 dict[文件名, 模型]。"""
    if not directory.is_dir():
        raise BenchmarkValidationError(f"目录不存在: {directory}")
    return {p.name: loader(p) for p in sorted(directory.glob("*.json"))}


def load_benchmark_suite(
    root: str | Path = "benchmark",
    *,
    allow_non_independent_authoring: bool = False,
) -> BenchmarkSuite:
    """加载并全量校验 Benchmark 数据集（设计文档 §13 校验清单），任何违规 fail-fast。

    - 结构：root/{cases,gold,manifests}/*.json
    - 配对：case.gold_ref ↔ gold 文件一一对应，且 gold.case_id == case.case_id
    - 引用：manifest.cases 必须全部存在于已加载 case 集合
    - 版本：manifest.benchmark_version 与其引用的每个 case 一致
    - 正式态：默认要求所有 gold_authoring == HUMAN_INDEPENDENT（§3 机检），
      allow_non_independent_authoring=True 仅供素材整理阶段显式放行
    - 错误处理：收集全部问题后一次性抛 BenchmarkValidationError（对使用者友好，对流程 fail-fast）
    """
    root = Path(root)
    cases_by_file = _load_dir(root / "cases", load_case)
    golds_by_file = _load_dir(root / "gold", load_gold)
    manifests_by_file = _load_dir(root / "manifests", load_manifest)

    errors: list[str] = []
    suite = BenchmarkSuite(root=root)

    # 1. case 索引 + 跨文件 duplicate case_id + 需求原文存在性（输入完整性）
    for fname, case in cases_by_file.items():
        if case.case_id in suite.cases:
            errors.append(f"case_id 重复: {case.case_id}（{fname}）")
            continue
        if not (root / "cases" / case.requirement_file).is_file():
            errors.append(f"case {case.case_id}: requirement_file 不存在: cases/{case.requirement_file}")
        suite.cases[case.case_id] = case
    if not suite.cases:
        errors.append(f"{root / 'cases'}: 至少需要一个 BenchmarkCase")

    # 2. gold 索引 + duplicate case_id
    golds_by_case: dict[str, BenchmarkGold] = {}
    for fname, gold in golds_by_file.items():
        if gold.case_id in golds_by_case:
            errors.append(f"gold 配对的 case_id 重复: {gold.case_id}（{fname}）")
            continue
        golds_by_case[gold.case_id] = gold

    # 3. case ↔ gold 一对一配对
    for case in suite.cases.values():
        gold = golds_by_file.get(case.gold_ref)
        if gold is None:
            errors.append(f"case {case.case_id}: gold_ref 文件不存在: gold/{case.gold_ref}")
            continue
        if gold.case_id != case.case_id:
            errors.append(f"case {case.case_id}: gold/{case.gold_ref} 的 case_id={gold.case_id} 不配对")
            continue
        suite.golds[case.case_id] = gold
    referenced = {c.gold_ref for c in suite.cases.values()}
    for fname in golds_by_file:
        if fname not in referenced:
            errors.append(f"gold 文件 {fname} 未被任何 case 的 gold_ref 引用（孤儿 Gold）")

    # 4. Gold 正式态（human_independent 机检）
    if not allow_non_independent_authoring:
        for cid, gold in suite.golds.items():
            if gold.gold_authoring != GoldAuthoring.HUMAN_INDEPENDENT:
                errors.append(
                    f"gold(case={cid}): gold_authoring={gold.gold_authoring.value}，"
                    "正式评价要求 human_independent（设计文档 §3；素材整理可显式放行）"
                )

    # 5. manifest 引用完整性 + benchmark_version 一致性
    for fname, manifest in manifests_by_file.items():
        if manifest.manifest_id in suite.manifests:
            errors.append(f"manifest_id 重复: {manifest.manifest_id}（{fname}）")
            continue
        suite.manifests[manifest.manifest_id] = manifest
        for cid in manifest.cases:
            case = suite.cases.get(cid)
            if case is None:
                errors.append(f"manifest {fname}: 引用不存在的 case_id: {cid}")
            elif case.benchmark_version != manifest.benchmark_version:
                errors.append(
                    f"manifest {fname}: benchmark_version 不一致 "
                    f"(manifest={manifest.benchmark_version}, case={cid}={case.benchmark_version})"
                )

    if errors:
        raise BenchmarkValidationError("Benchmark 数据集校验失败:\n- " + "\n- ".join(errors))
    return suite
