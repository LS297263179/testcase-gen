"""Step 11 S2：Benchmark 数据契约单测（schema / loader / 配对校验）。

对应 docs/v2/step11-benchmark-evaluation.md §13 校验清单。
覆盖：合法 case/gold/manifest、缺字段、类型错误、多余字段、case/gold 不成对、
manifest 引用不存在 case、gold_authoring 正式态、枚举非法、内部引用完整性。
"""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.schemas.common import EntityBase, Technique
from core.v2.eval.schema import (
    BenchmarkCase,
    BenchmarkGold,
    BenchmarkManifest,
    BenchmarkRunStatus,
    BenchmarkValidationError,
    CaseDifficulty,
    GoldAuthoring,
    MetricProvenance,
    StrategyFeature,
    load_benchmark_suite,
    load_case,
    load_gold,
    load_manifest,
)

_BENCHMARK_ROOT = Path(__file__).resolve().parents[1] / "benchmark"
_DEMO_CASE_FILE = "bc_demo_login.json"
_DEMO_GOLD_FILE = "bc_demo_login.gold.json"
_DEMO_MANIFEST_FILE = "bm_demo.json"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ============================================================
# fixtures：以仓库内 demo fixture 为合法基线（负例在其上 mutate）
# ============================================================


@pytest.fixture
def demo_case_dict() -> dict:
    return copy.deepcopy(_read_json(_BENCHMARK_ROOT / "cases" / _DEMO_CASE_FILE))


@pytest.fixture
def demo_gold_dict() -> dict:
    return copy.deepcopy(_read_json(_BENCHMARK_ROOT / "gold" / _DEMO_GOLD_FILE))


@pytest.fixture
def demo_manifest_dict() -> dict:
    return copy.deepcopy(_read_json(_BENCHMARK_ROOT / "manifests" / _DEMO_MANIFEST_FILE))


@pytest.fixture
def valid_suite(demo_case_dict, demo_gold_dict, demo_manifest_dict) -> dict:
    """合法数据集（cases/gold/manifests 三目录内容）。"""
    return {
        "cases": {_DEMO_CASE_FILE: demo_case_dict, "bc_demo_login_requirement.md": None},
        "gold": {_DEMO_GOLD_FILE: demo_gold_dict},
        "manifests": {_DEMO_MANIFEST_FILE: demo_manifest_dict},
    }


def _write_suite(tmp_path: Path, suite: dict) -> Path:
    """把数据集写到 tmp_path（value 为 None 的条目按占位文本写 .md）。"""
    for sub, files in suite.items():
        d = tmp_path / sub
        d.mkdir(parents=True, exist_ok=True)
        for name, payload in files.items():
            if payload is None:
                (d / name).write_text("# 占位需求\n手机号必须为 11 位数字\n", encoding="utf-8")
            else:
                (d / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return tmp_path


# ============================================================
# eval 私有枚举
# ============================================================


class TestBenchmarkEnums:
    def test_run_status_five_states(self):
        """核查结论 2：五态冻结，且不与 common.RunStatus 混用"""
        assert {s.value for s in BenchmarkRunStatus} == {
            "completed",
            "llm_failure",
            "runtime_failure",
            "evaluation_failure",
            "input_invalid",
        }

    def test_metric_provenance_three_states(self):
        """核查结论 3：CODE / LLM / MIXED"""
        assert {p.value for p in MetricProvenance} == {"code", "llm", "mixed"}

    def test_run_status_rejects_unknown(self):
        with pytest.raises(ValueError):
            BenchmarkRunStatus("quality_failure")  # 质量失败不是运行状态（五态冻结）

    def test_difficulty_and_authoring_values(self):
        assert {d.value for d in CaseDifficulty} == {"easy", "medium", "hard"}
        assert {a.value for a in GoldAuthoring} == {"human_independent", "human_assisted"}

    def test_strategy_feature_excludes_step4_deferred_techniques(self):
        """核查结论 7：只允许 Step 4 实际支持的特征（state_flow 等不得进入）"""
        assert "state_flow" not in {f.value for f in StrategyFeature}
        assert "decision_table" not in {f.value for f in StrategyFeature}
        assert StrategyFeature.NUMERIC_RANGE.value == "numeric_range"
        assert StrategyFeature.PERMISSION_RULE.value == "permission_rule"


class TestNoEntityBaseCoupling:
    """S2 冻结：Benchmark 模型是文件级数据契约，不是 DB 实体"""

    @pytest.mark.parametrize("model_cls", [BenchmarkCase, BenchmarkGold, BenchmarkManifest])
    def test_not_entity_subclass(self, model_cls):
        assert not issubclass(model_cls, EntityBase)

    @pytest.mark.parametrize("model_cls", [BenchmarkCase, BenchmarkGold, BenchmarkManifest])
    def test_no_db_entity_fields(self, model_cls):
        fields = set(model_cls.model_fields)
        assert not {"id", "created_at", "updated_at", "schema_version"} & fields


# ============================================================
# BenchmarkCase
# ============================================================


class TestBenchmarkCase:
    def test_demo_case_valid(self, demo_case_dict):
        case = BenchmarkCase.model_validate(demo_case_dict)
        assert case.case_id == "bc_demo_login"
        assert case.difficulty == CaseDifficulty.EASY

    def test_roundtrip(self, demo_case_dict):
        case = BenchmarkCase.model_validate(demo_case_dict)
        assert BenchmarkCase.model_validate(case.model_dump(mode="json")) == case

    @pytest.mark.parametrize(
        "field", ["case_id", "title", "requirement_file", "domain", "difficulty", "benchmark_version", "gold_ref"]
    )
    def test_missing_required_field_fails(self, demo_case_dict, field):
        del demo_case_dict[field]
        with pytest.raises(ValidationError):
            BenchmarkCase.model_validate(demo_case_dict)

    def test_type_error_difficulty(self, demo_case_dict):
        demo_case_dict["difficulty"] = 7
        with pytest.raises(ValidationError):
            BenchmarkCase.model_validate(demo_case_dict)

    def test_type_error_case_id_not_int(self, demo_case_dict):
        demo_case_dict["case_id"] = 123
        with pytest.raises(ValidationError):
            BenchmarkCase.model_validate(demo_case_dict)

    def test_type_error_tags_not_list(self, demo_case_dict):
        demo_case_dict["tags"] = "demo"
        with pytest.raises(ValidationError):
            BenchmarkCase.model_validate(demo_case_dict)

    def test_extra_field_forbidden(self, demo_case_dict):
        demo_case_dict["unknown_key"] = "boom"
        with pytest.raises(ValidationError):
            BenchmarkCase.model_validate(demo_case_dict)

    @pytest.mark.parametrize("bad_id", ["BC_Login", "a", "-lead", "x" * 65])
    def test_case_id_pattern_enforced(self, demo_case_dict, bad_id):
        demo_case_dict["case_id"] = bad_id
        with pytest.raises(ValidationError):
            BenchmarkCase.model_validate(demo_case_dict)

    @pytest.mark.parametrize("bad_ref", ["gold.txt", "sub/dir.gold.json", ""])
    def test_gold_ref_must_be_json_filename(self, demo_case_dict, bad_ref):
        demo_case_dict["gold_ref"] = bad_ref
        with pytest.raises(ValidationError):
            BenchmarkCase.model_validate(demo_case_dict)


# ============================================================
# BenchmarkGold
# ============================================================


class TestBenchmarkGold:
    def test_demo_gold_valid(self, demo_gold_dict):
        gold = BenchmarkGold.model_validate(demo_gold_dict)
        assert gold.case_id == "bc_demo_login"
        assert gold.gold_authoring == GoldAuthoring.HUMAN_INDEPENDENT
        assert len(gold.gold_requirements) == 2

    def test_roundtrip(self, demo_gold_dict):
        gold = BenchmarkGold.model_validate(demo_gold_dict)
        assert BenchmarkGold.model_validate(gold.model_dump(mode="json")) == gold

    @pytest.mark.parametrize(
        "field",
        [
            "gold_version",
            "case_id",
            "authoring_note",
            "gold_authoring",
            "gold_requirements",
            "critical_scenarios",
            "obligations_expected",
            "strategy_expectations",
            "reference_cases",
            "acceptable_variants",
            "forbidden_patterns",
        ],
    )
    def test_missing_required_key_fails(self, demo_gold_dict, field):
        """必答题：键必须出现（可为空列表 = 显式声明无此项）"""
        del demo_gold_dict[field]
        with pytest.raises(ValidationError):
            BenchmarkGold.model_validate(demo_gold_dict)

    def test_empty_optional_lists_allowed(self, demo_gold_dict):
        demo_gold_dict["critical_scenarios"] = []
        demo_gold_dict["reference_cases"] = []
        BenchmarkGold.model_validate(demo_gold_dict)  # 不抛错

    def test_gold_requirements_not_empty_required(self, demo_gold_dict):
        demo_gold_dict["gold_requirements"] = []
        with pytest.raises(ValidationError):
            BenchmarkGold.model_validate(demo_gold_dict)

    def test_type_error(self, demo_gold_dict):
        demo_gold_dict["gold_requirements"] = "gr-1"
        with pytest.raises(ValidationError):
            BenchmarkGold.model_validate(demo_gold_dict)

    def test_extra_field_forbidden(self, demo_gold_dict):
        demo_gold_dict["authoring_source"] = "v2_self_generated"
        with pytest.raises(ValidationError):
            BenchmarkGold.model_validate(demo_gold_dict)

    def test_invalid_gold_authoring_enum(self, demo_gold_dict):
        demo_gold_dict["gold_authoring"] = "ai_generated"
        with pytest.raises(ValidationError):
            BenchmarkGold.model_validate(demo_gold_dict)

    def test_duplicate_gold_id_fails(self, demo_gold_dict):
        demo_gold_dict["gold_requirements"][1]["gold_id"] = demo_gold_dict["gold_requirements"][0]["gold_id"]
        with pytest.raises(ValidationError, match="重复"):
            BenchmarkGold.model_validate(demo_gold_dict)

    def test_scenario_dangling_gold_ref_fails(self, demo_gold_dict):
        demo_gold_dict["critical_scenarios"][0]["requirement_gold_ids"] = ["gr-not-exist"]
        with pytest.raises(ValidationError, match="不存在"):
            BenchmarkGold.model_validate(demo_gold_dict)

    def test_variant_dangling_gold_ref_fails(self, demo_gold_dict):
        demo_gold_dict["acceptable_variants"][0]["for_gold_id"] = "gr-not-exist"
        with pytest.raises(ValidationError, match="不存在"):
            BenchmarkGold.model_validate(demo_gold_dict)

    def test_obligation_dangling_gold_ref_fails(self, demo_gold_dict):
        demo_gold_dict["obligations_expected"][0]["requirement_gold_id"] = "gr-not-exist"
        with pytest.raises(ValidationError, match="不存在"):
            BenchmarkGold.model_validate(demo_gold_dict)

    def test_strategy_feature_technique_mismatch_fails(self, demo_gold_dict):
        """核查结论 7：feature→technique 映射冻结（enum_field 只能等价类）"""
        exp = demo_gold_dict["strategy_expectations"][0]
        exp["feature"] = "enum_field"
        exp["technique"] = Technique.BOUNDARY_VALUE.value
        with pytest.raises(ValidationError, match="technique"):
            BenchmarkGold.model_validate(demo_gold_dict)

    def test_strategy_invalid_feature_fails(self, demo_gold_dict):
        demo_gold_dict["strategy_expectations"][0]["feature"] = "state_flow"
        with pytest.raises(ValidationError):
            BenchmarkGold.model_validate(demo_gold_dict)


# ============================================================
# BenchmarkManifest
# ============================================================


class TestBenchmarkManifest:
    def test_demo_manifest_valid(self, demo_manifest_dict):
        m = BenchmarkManifest.model_validate(demo_manifest_dict)
        assert m.manifest_id == "bm-demo"
        assert m.repeat == 1

    @pytest.mark.parametrize("field", ["manifest_id", "benchmark_version", "cases"])
    def test_missing_required_field_fails(self, demo_manifest_dict, field):
        del demo_manifest_dict[field]
        with pytest.raises(ValidationError):
            BenchmarkManifest.model_validate(demo_manifest_dict)

    def test_extra_field_forbidden(self, demo_manifest_dict):
        demo_manifest_dict["ci_gate"] = True
        with pytest.raises(ValidationError):
            BenchmarkManifest.model_validate(demo_manifest_dict)

    def test_duplicate_cases_rejected(self, demo_manifest_dict):
        demo_manifest_dict["cases"] = ["bc_demo_login", "bc_demo_login"]
        with pytest.raises(ValidationError, match="重复"):
            BenchmarkManifest.model_validate(demo_manifest_dict)

    def test_empty_cases_rejected(self, demo_manifest_dict):
        demo_manifest_dict["cases"] = []
        with pytest.raises(ValidationError):
            BenchmarkManifest.model_validate(demo_manifest_dict)

    @pytest.mark.parametrize("bad_repeat", [0, -1])
    def test_repeat_ge_one(self, demo_manifest_dict, bad_repeat):
        demo_manifest_dict["repeat"] = bad_repeat
        with pytest.raises(ValidationError):
            BenchmarkManifest.model_validate(demo_manifest_dict)


# ============================================================
# 数据集 loader：配对 / 引用 / 正式态 fail-fast
# ============================================================


class TestSuiteLoader:
    def test_load_repo_benchmark_root(self):
        """仓库内 benchmark/ 全量数据（demo + bench-v0.1 正式集）必须整体通过校验；
        断言写成增量安全形式，Phase 2 扩充正式 case 无需再改本测试"""
        suite = load_benchmark_suite(_BENCHMARK_ROOT)
        assert {"bc_demo_login", "bc_02_refund_order"} <= set(suite.cases)
        assert set(suite.golds) == set(suite.cases)  # loader 保证 1:1 配对，这里再显式锁定
        assert {"bm-demo", "bm-bench-v0-1"} <= set(suite.manifests)
        assert all(g.gold_authoring == GoldAuthoring.HUMAN_INDEPENDENT for g in suite.golds.values())
        # demo（bench-v0.0-demo）与正式集（bench-v0.1）版本隔离
        assert suite.cases["bc_demo_login"].benchmark_version == "bench-v0.0-demo"
        assert suite.cases["bc_02_refund_order"].benchmark_version == "bench-v0.1"
        assert "bc_02_refund_order" in suite.manifests["bm-bench-v0-1"].cases

    def test_valid_tmp_suite(self, tmp_path, valid_suite):
        root = _write_suite(tmp_path, valid_suite)
        suite = load_benchmark_suite(root)
        assert suite.cases["bc_demo_login"].gold_ref == _DEMO_GOLD_FILE

    def test_case_without_paired_gold_fails(self, tmp_path, valid_suite):
        del valid_suite["gold"][_DEMO_GOLD_FILE]
        root = _write_suite(tmp_path, valid_suite)
        with pytest.raises(BenchmarkValidationError, match="gold_ref 文件不存在"):
            load_benchmark_suite(root)

    def test_gold_case_id_mismatch_fails(self, tmp_path, valid_suite):
        valid_suite["gold"][_DEMO_GOLD_FILE]["case_id"] = "bc_other"
        root = _write_suite(tmp_path, valid_suite)
        with pytest.raises(BenchmarkValidationError, match="不配对"):
            load_benchmark_suite(root)

    def test_orphan_gold_fails(self, tmp_path, valid_suite):
        orphan = copy.deepcopy(valid_suite["gold"][_DEMO_GOLD_FILE])
        orphan["case_id"] = "bc_orphan"
        valid_suite["gold"]["bc_orphan.gold.json"] = orphan
        root = _write_suite(tmp_path, valid_suite)
        with pytest.raises(BenchmarkValidationError, match="孤儿 Gold"):
            load_benchmark_suite(root)

    def test_manifest_unknown_case_fails_fast(self, tmp_path, valid_suite):
        valid_suite["manifests"][_DEMO_MANIFEST_FILE]["cases"] = ["bc_demo_login", "bc_not_exist"]
        root = _write_suite(tmp_path, valid_suite)
        with pytest.raises(BenchmarkValidationError, match="引用不存在的 case_id"):
            load_benchmark_suite(root)

    def test_manifest_version_mismatch_fails(self, tmp_path, valid_suite):
        valid_suite["manifests"][_DEMO_MANIFEST_FILE]["benchmark_version"] = "bench-v9.9"
        root = _write_suite(tmp_path, valid_suite)
        with pytest.raises(BenchmarkValidationError, match="benchmark_version 不一致"):
            load_benchmark_suite(root)

    def test_duplicate_case_id_across_files_fails(self, tmp_path, valid_suite):
        valid_suite["cases"]["bc_demo_login_copy.json"] = copy.deepcopy(valid_suite["cases"][_DEMO_CASE_FILE])
        root = _write_suite(tmp_path, valid_suite)
        with pytest.raises(BenchmarkValidationError, match="case_id 重复"):
            load_benchmark_suite(root)

    def test_missing_requirement_file_fails(self, tmp_path, valid_suite):
        del valid_suite["cases"]["bc_demo_login_requirement.md"]
        root = _write_suite(tmp_path, valid_suite)
        with pytest.raises(BenchmarkValidationError, match="requirement_file 不存在"):
            load_benchmark_suite(root)

    def test_empty_cases_dir_fails(self, tmp_path):
        root = _write_suite(tmp_path, {"cases": {}, "gold": {}, "manifests": {}})
        with pytest.raises(BenchmarkValidationError, match="至少需要一个 BenchmarkCase"):
            load_benchmark_suite(root)

    def test_missing_dir_fails(self, tmp_path):
        with pytest.raises(BenchmarkValidationError, match="目录不存在"):
            load_benchmark_suite(tmp_path)

    def test_broken_json_fails_with_context(self, tmp_path, valid_suite):
        root = _write_suite(tmp_path, valid_suite)
        # 把合法 case 文件覆盖成坏 JSON，验证报错带文件名上下文
        (root / "cases" / _DEMO_CASE_FILE).write_text("{{{not json", encoding="utf-8")
        with pytest.raises(BenchmarkValidationError, match="JSON 解析失败"):
            load_benchmark_suite(root)

    def test_non_independent_gold_rejected_by_default(self, tmp_path, valid_suite):
        """核查结论 4：正式态要求 human_independent（系统自产 Gold 闭环禁令的机检）"""
        valid_suite["gold"][_DEMO_GOLD_FILE]["gold_authoring"] = GoldAuthoring.HUMAN_ASSISTED.value
        root = _write_suite(tmp_path, valid_suite)
        with pytest.raises(BenchmarkValidationError, match="human_independent"):
            load_benchmark_suite(root)

    def test_non_independent_gold_allowed_in_material_mode(self, tmp_path, valid_suite):
        valid_suite["gold"][_DEMO_GOLD_FILE]["gold_authoring"] = GoldAuthoring.HUMAN_ASSISTED.value
        root = _write_suite(tmp_path, valid_suite)
        suite = load_benchmark_suite(root, allow_non_independent_authoring=True)
        assert suite.golds["bc_demo_login"].gold_authoring == GoldAuthoring.HUMAN_ASSISTED

    def test_errors_are_aggregated(self, tmp_path, valid_suite):
        """多处违规一次性全部报出（对使用者友好，对流程仍 fail-fast）"""
        del valid_suite["gold"][_DEMO_GOLD_FILE]
        valid_suite["manifests"][_DEMO_MANIFEST_FILE]["cases"] = ["bc_ghost"]
        root = _write_suite(tmp_path, valid_suite)
        with pytest.raises(BenchmarkValidationError) as ei:
            load_benchmark_suite(root)
        msg = str(ei.value)
        assert "gold_ref 文件不存在" in msg
        assert "引用不存在的 case_id" in msg


class TestGoldMatchKeysContract:
    """S3 修正 2/3 的 Gold 契约扩展：match_keys 冻结指纹 + min_covered_points"""

    def test_match_keys_valid(self, demo_gold_dict):
        demo_gold_dict["gold_requirements"][0]["match_keys"] = {
            "identity_fingerprints": ["ri_" + "a" * 32],
            "variants": ["手机号为11位数字"],
        }
        gold = BenchmarkGold.model_validate(demo_gold_dict)
        assert gold.gold_requirements[0].match_keys.variants == ["手机号为11位数字"]

    @pytest.mark.parametrize("bad_fp", ["ri_short", "xx_" + "a" * 32, "ri_" + "Z" * 32])
    def test_match_keys_fingerprint_format(self, demo_gold_dict, bad_fp):
        demo_gold_dict["gold_requirements"][0]["match_keys"] = {"identity_fingerprints": [bad_fp]}
        with pytest.raises(ValidationError, match="ri_"):
            BenchmarkGold.model_validate(demo_gold_dict)

    def test_match_keys_requires_fingerprints(self, demo_gold_dict):
        demo_gold_dict["gold_requirements"][0]["match_keys"] = {"identity_fingerprints": []}
        with pytest.raises(ValidationError):
            BenchmarkGold.model_validate(demo_gold_dict)

    def test_match_keys_extra_field_forbidden(self, demo_gold_dict):
        demo_gold_dict["gold_requirements"][0]["match_keys"] = {
            "identity_fingerprints": ["ri_" + "a" * 32],
            "similarity_threshold": 0.6,  # 禁止在 Gold 里冻结“相似度即命中”类参数
        }
        with pytest.raises(ValidationError):
            BenchmarkGold.model_validate(demo_gold_dict)

    def test_obligation_min_covered_points(self, demo_gold_dict):
        demo_gold_dict["obligations_expected"][0]["min_covered_points"] = 3
        gold = BenchmarkGold.model_validate(demo_gold_dict)
        assert gold.obligations_expected[0].min_covered_points == 3

    @pytest.mark.parametrize("bad_min", [0, -2])
    def test_obligation_min_covered_points_ge_one(self, demo_gold_dict, bad_min):
        demo_gold_dict["obligations_expected"][0]["min_covered_points"] = bad_min
        with pytest.raises(ValidationError):
            BenchmarkGold.model_validate(demo_gold_dict)

    def test_demo_gold_without_match_keys_still_valid(self, demo_gold_dict):
        """match_keys 可选：缺省时匹配层走 statement 重算 fallback（向后兼容 S2 fixture）"""
        gold = BenchmarkGold.model_validate(demo_gold_dict)
        assert all(gr.match_keys is None for gr in gold.gold_requirements)


class TestSingleFileLoaders:
    def test_loaders_read_repo_demo_files(self):
        case = load_case(_BENCHMARK_ROOT / "cases" / _DEMO_CASE_FILE)
        gold = load_gold(_BENCHMARK_ROOT / "gold" / _DEMO_GOLD_FILE)
        manifest = load_manifest(_BENCHMARK_ROOT / "manifests" / _DEMO_MANIFEST_FILE)
        assert case.gold_ref == gold.model_dump()["case_id"] + ".gold.json"
        assert manifest.cases == [case.case_id]
