"""V2 IR 校验器测试 - 去重、结构一致性检查、置信度分级、seq 重排。"""

from core.schemas import (
    ConfidenceLevel,
    DataType,
    FieldSpec,
    RequirementItem,
    RequirementItemType,
)
from core.v2.validator import _normalize_statement, validate_ir

VID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def _item(statement, module="m", type=RequirementItemType.FUNCTION, confidence=1.0, **kw):
    return RequirementItem(
        version_id=VID, seq=1, type=type, module=module, statement=statement, confidence=confidence, **kw
    )


class TestDedupe:
    def test_exact_duplicate_removed(self):
        r = validate_ir([_item("登录成功"), _item("登录成功")])
        assert len(r.items) == 1
        assert r.duplicates_removed == 1

    def test_normalized_duplicate_removed(self):
        # 空白/标点差异视为同一需求项
        r = validate_ir([_item("登录 成功！"), _item("登录成功")])
        assert r.duplicates_removed == 1

    def test_different_module_not_deduped(self):
        r = validate_ir([_item("登录成功", module="A"), _item("登录成功", module="B")])
        assert len(r.items) == 2

    def test_seq_resequenced_after_dedupe(self):
        r = validate_ir([_item("a"), _item("a"), _item("b")])
        assert [it.seq for it in r.items] == [1, 2]


class TestStructuralWarnings:
    def test_data_field_without_fields_warns(self):
        r = validate_ir([_item("字段校验", type=RequirementItemType.DATA_FIELD)])
        assert any("data_field" in w for w in r.warnings)

    def test_data_field_with_fields_no_warn(self):
        it = _item(
            "字段校验",
            type=RequirementItemType.DATA_FIELD,
            fields=[FieldSpec(name="x", label="X", data_type=DataType.STRING)],
        )
        assert validate_ir([it]).warnings == []

    def test_permission_without_permissions_warns(self):
        r = validate_ir([_item("权限控制", type=RequirementItemType.PERMISSION)])
        assert any("permission" in w for w in r.warnings)

    def test_business_rule_without_rules_warns(self):
        r = validate_ir([_item("计算规则", type=RequirementItemType.BUSINESS_RULE)])
        assert any("business_rule" in w for w in r.warnings)


class TestConfidence:
    def test_low_confidence_flagged(self):
        it = _item("含糊需求", confidence=0.3)
        r = validate_ir([it])
        assert it.confidence_level == ConfidenceLevel.LOW
        assert it.id in r.low_confidence_ids

    def test_high_confidence_not_flagged(self):
        r = validate_ir([_item("明确需求", confidence=0.95)])
        assert r.low_confidence_ids == []


class TestNormalizeStatement:
    def test_strips_punct_and_space(self):
        assert _normalize_statement("  登录 成功！ ") == "登录成功"

    def test_case_insensitive(self):
        assert _normalize_statement("Login OK") == _normalize_statement("login ok")
