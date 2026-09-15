"""V2 Step 3 测试点校验器测试 - 硬性覆写、item_ids 归一、去重、覆盖率、语义 warning。"""

from core.schemas import (
    EntityStatus,
    GenerationScope,
    Priority,
    Provenance,
    RequirementItem,
    RequirementItemType,
    TestDimension,
    TestPoint,
)
from core.v2.tp_validator import (
    build_coverage_report,
    dedupe_points,
    validate_phase_a,
    validate_phase_b,
    warn_semantic_duplicates,
)

VERSION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def _make_item(item_id: str, module: str = "登录", statement: str = "s") -> RequirementItem:
    return RequirementItem(
        id=item_id,
        version_id=VERSION_ID,
        seq=1,
        type=RequirementItemType.FUNCTION,
        module=module,
        statement=statement,
        confidence=0.9,
    )


# ============================================================
# Phase A 校验
# ============================================================


class TestValidatePhaseA:
    def test_hard_overwrites_step3_constraints(self):
        """Step 3 硬性约束：provenance=LLM, technique=None, obligation_id=None, status=DRAFT, scope=ITEM"""
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        # LLM 恶意给了 technique/obligation_id/provenance/status → 代码必须覆写
        raw = {
            "module": "乱写的模块",  # 应被覆写为 item.module
            "subcategory": "输入校验",
            "title": "手机号长度",
            "description": "验证 11 位",
            "dimension": "boundary",
            "priority": "P1",
            "technique": "boundary_value",  # 应被清空
            "obligation_id": "01ARZ3NDEKTSV4RRFFQ69G5FB0",  # 应被清空
            "provenance": "strategy",  # 应被覆写为 llm
            "status": "confirmed",  # 应被覆写为 draft
            "generation_scope": "cross_item",  # 应被覆写为 item
        }
        result = validate_phase_a([raw], item=item)
        assert len(result.points) == 1
        tp = result.points[0]
        assert tp.provenance == Provenance.LLM
        assert tp.technique is None
        assert tp.obligation_id is None
        assert tp.status == EntityStatus.DRAFT
        assert tp.generation_scope == GenerationScope.ITEM

    def test_item_ids_forced_to_current_item(self):
        """Phase A 强制覆写 item_ids = [当前 item.id]，忽略 LLM 给的"""
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        raw = {
            "module": "登录",
            "subcategory": "s",
            "title": "t",
            "description": "d",
            "dimension": "functional",
            "priority": "P1",
            "item_ids": ["乱写的id", "01ARZ3NDEKTSV4RRFFQ69G5FB0"],  # 应被忽略
        }
        result = validate_phase_a([raw], item=item)
        assert result.points[0].item_ids == [item.id]

    def test_module_overwritten_from_item(self):
        """Phase A 的 module 必须来自 item.module，LLM 乱写会被覆写"""
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV", module="用户登录")
        raw = {
            "module": "订单管理",  # 乱写
            "subcategory": "s",
            "title": "t",
            "description": "d",
            "dimension": "functional",
            "priority": "P1",
        }
        result = validate_phase_a([raw], item=item)
        assert result.points[0].module == "用户登录"

    def test_invalid_dimension_falls_back(self):
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        raw = {
            "module": "登录",
            "subcategory": "s",
            "title": "t",
            "description": "d",
            "dimension": "乱写的维度",
            "priority": "P1",
        }
        result = validate_phase_a([raw], item=item)
        assert result.points[0].dimension == TestDimension.FUNCTIONAL

    def test_invalid_priority_falls_back(self):
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        raw = {
            "module": "登录",
            "subcategory": "s",
            "title": "t",
            "description": "d",
            "dimension": "functional",
            "priority": "P99",
        }
        result = validate_phase_a([raw], item=item)
        assert result.points[0].priority == Priority.P1

    def test_missing_title_dropped(self):
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        raw = {"module": "m", "subcategory": "s", "description": "d", "dimension": "functional", "priority": "P1"}
        result = validate_phase_a([raw], item=item)
        assert result.points == []
        assert any("title" in i for i in result.issues)

    def test_missing_description_dropped(self):
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        raw = {"module": "m", "subcategory": "s", "title": "t", "dimension": "functional", "priority": "P1"}
        result = validate_phase_a([raw], item=item)
        assert result.points == []
        assert any("description" in i for i in result.issues)

    def test_empty_subcategory_defaults(self):
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        raw = {
            "module": "登录",
            "subcategory": "",
            "title": "t",
            "description": "d",
            "dimension": "functional",
            "priority": "P1",
        }
        result = validate_phase_a([raw], item=item)
        assert result.points[0].subcategory == "未分类"

    def test_fingerprint_computed(self):
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        raw = {
            "module": "登录",
            "subcategory": "s",
            "title": "t",
            "description": "d",
            "dimension": "functional",
            "priority": "P1",
        }
        result = validate_phase_a([raw], item=item)
        tp = result.points[0]
        assert tp.fingerprint is not None
        assert tp.fingerprint.startswith("tp_")
        assert len(tp.fingerprint) == 35  # "tp_" + 32 hex

    def test_version_id_injected_from_item(self):
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        raw = {
            "module": "登录",
            "subcategory": "s",
            "title": "t",
            "description": "d",
            "dimension": "functional",
            "priority": "P1",
        }
        result = validate_phase_a([raw], item=item)
        assert result.points[0].version_id == VERSION_ID

    def test_noise_fields_dropped(self):
        """LLM 多给的噪声键（不在白名单）应被丢弃"""
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        raw = {
            "module": "登录",
            "subcategory": "s",
            "title": "t",
            "description": "d",
            "dimension": "functional",
            "priority": "P1",
            "unknown_field": "boom",  # 应被白名单过滤
            "run_id": "乱写",  # 应被过滤（run_id 由 orchestrator 注入）
        }
        result = validate_phase_a([raw], item=item)
        assert len(result.points) == 1
        # 不报错、字段被过滤即算通过


# ============================================================
# Phase B 校验
# ============================================================


class TestValidatePhaseB:
    def test_filters_invalid_item_ids(self):
        items = [
            _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV", module="订单"),
            _make_item("01ARZ3NDEKTSV4RRFFQ69G5FB0", module="订单"),
        ]
        raw = {
            "item_ids": ["01ARZ3NDEKTSV4RRFFQ69G5FAV", "乱写的id", "01ARZ3NDEKTSV4RRFFQ69G5FB0"],
            "module": "订单",
            "subcategory": "字段联动",
            "title": "总价与库存",
            "description": "d",
            "dimension": "linkage",
            "priority": "P0",
        }
        result = validate_phase_b([raw], items=items)
        assert len(result.points) == 1
        # 非法 id 被过滤，剩下 2 个合法 id
        assert set(result.points[0].item_ids) == {"01ARZ3NDEKTSV4RRFFQ69G5FAV", "01ARZ3NDEKTSV4RRFFQ69G5FB0"}

    def test_drops_when_item_ids_less_than_2_after_filter(self):
        items = [
            _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV", module="订单"),
            _make_item("01ARZ3NDEKTSV4RRFFQ69G5FB0", module="订单"),
        ]
        raw = {
            "item_ids": ["01ARZ3NDEKTSV4RRFFQ69G5FAV", "乱写的id"],  # 过滤后只剩 1 个
            "module": "订单",
            "subcategory": "s",
            "title": "t",
            "description": "d",
            "dimension": "linkage",
            "priority": "P1",
        }
        result = validate_phase_b([raw], items=items)
        assert result.points == []
        assert any("<2" in i or "丢弃" in i for i in result.issues)

    def test_dedupes_item_ids_preserving_order(self):
        items = [
            _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV", module="订单"),
            _make_item("01ARZ3NDEKTSV4RRFFQ69G5FB0", module="订单"),
        ]
        raw = {
            "item_ids": [
                "01ARZ3NDEKTSV4RRFFQ69G5FAV",
                "01ARZ3NDEKTSV4RRFFQ69G5FB0",
                "01ARZ3NDEKTSV4RRFFQ69G5FAV",  # 重复
            ],
            "module": "订单",
            "subcategory": "s",
            "title": "t",
            "description": "d",
            "dimension": "linkage",
            "priority": "P1",
        }
        result = validate_phase_b([raw], items=items)
        assert result.points[0].item_ids == [
            "01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "01ARZ3NDEKTSV4RRFFQ69G5FB0",
        ]

    def test_module_must_be_in_items_modules(self):
        """module 不在 items.module 集合时，取第一个 item 的 module 兜底"""
        items = [
            _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV", module="订单"),
            _make_item("01ARZ3NDEKTSV4RRFFQ69G5FB0", module="支付"),
        ]
        raw = {
            "item_ids": ["01ARZ3NDEKTSV4RRFFQ69G5FAV", "01ARZ3NDEKTSV4RRFFQ69G5FB0"],
            "module": "乱写的模块",  # 不在 items.module 集合
            "subcategory": "s",
            "title": "t",
            "description": "d",
            "dimension": "linkage",
            "priority": "P1",
        }
        result = validate_phase_b([raw], items=items)
        # 兜底为第一个 item 的 module
        assert result.points[0].module == "订单"

    def test_module_kept_when_valid(self):
        items = [
            _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV", module="订单"),
            _make_item("01ARZ3NDEKTSV4RRFFQ69G5FB0", module="支付"),
        ]
        raw = {
            "item_ids": ["01ARZ3NDEKTSV4RRFFQ69G5FAV", "01ARZ3NDEKTSV4RRFFQ69G5FB0"],
            "module": "支付",  # 合法
            "subcategory": "s",
            "title": "t",
            "description": "d",
            "dimension": "linkage",
            "priority": "P1",
        }
        result = validate_phase_b([raw], items=items)
        assert result.points[0].module == "支付"

    def test_hard_overwrites_step3_constraints(self):
        items = [
            _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV", module="订单"),
            _make_item("01ARZ3NDEKTSV4RRFFQ69G5FB0", module="订单"),
        ]
        raw = {
            "item_ids": ["01ARZ3NDEKTSV4RRFFQ69G5FAV", "01ARZ3NDEKTSV4RRFFQ69G5FB0"],
            "module": "订单",
            "subcategory": "s",
            "title": "t",
            "description": "d",
            "dimension": "linkage",
            "priority": "P1",
            "technique": "decision_table",  # 应被清空
            "provenance": "strategy",  # 应被覆写
        }
        result = validate_phase_b([raw], items=items)
        tp = result.points[0]
        assert tp.technique is None
        assert tp.provenance == Provenance.LLM
        assert tp.generation_scope == GenerationScope.CROSS_ITEM

    def test_version_id_explicit(self):
        items = [
            _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV", module="订单"),
            _make_item("01ARZ3NDEKTSV4RRFFQ69G5FB0", module="订单"),
        ]
        raw = {
            "item_ids": ["01ARZ3NDEKTSV4RRFFQ69G5FAV", "01ARZ3NDEKTSV4RRFFQ69G5FB0"],
            "module": "订单",
            "subcategory": "s",
            "title": "t",
            "description": "d",
            "dimension": "linkage",
            "priority": "P1",
        }
        other_vid = "01ARZ3NDEKTSV4RRFFQ69G5FZZ"
        result = validate_phase_b([raw], items=items, version_id=other_vid)
        assert result.points[0].version_id == other_vid


# ============================================================
# 去重
# ============================================================


class TestDedupe:
    def _tp(self, title: str, scope: GenerationScope = GenerationScope.ITEM, item_ids=None) -> TestPoint:
        return TestPoint(
            version_id=VERSION_ID,
            item_ids=item_ids or ["01ARZ3NDEKTSV4RRFFQ69G5FAV"],
            module="登录",
            subcategory="s",
            title=title,
            description="d",
            dimension=TestDimension.FUNCTIONAL,
            generation_scope=scope,
            fingerprint=f"tp_fake_{title}_{scope.value}",
        )

    def test_same_fingerprint_keeps_first(self):
        tp1 = self._tp("t1")
        tp2 = self._tp("t1")  # 同 fingerprint
        tp2.description = "更新后的描述"
        deduped, issues = dedupe_points([tp1, tp2])
        assert len(deduped) == 1
        assert deduped[0].description == "d"  # 保留首个
        assert any("重复" in i for i in issues)

    def test_different_fingerprints_all_kept(self):
        tps = [self._tp(f"t{i}") for i in range(3)]
        deduped, issues = dedupe_points(tps)
        assert len(deduped) == 3
        assert not issues

    def test_different_scope_not_deduped(self):
        """Phase A(item) 与 Phase B(cross_item) 即使 title 相同也不合并（业务身份天然不同）"""
        tp_item = self._tp("联动", scope=GenerationScope.ITEM, item_ids=["01ARZ3NDEKTSV4RRFFQ69G5FAV"])
        tp_cross = self._tp(
            "联动",
            scope=GenerationScope.CROSS_ITEM,
            item_ids=["01ARZ3NDEKTSV4RRFFQ69G5FAV", "01ARZ3NDEKTSV4RRFFQ69G5FB0"],
        )
        deduped, _ = dedupe_points([tp_item, tp_cross])
        assert len(deduped) == 2

    def test_missing_fingerprint_skipped(self):
        tp = self._tp("t1")
        tp.fingerprint = None
        deduped, issues = dedupe_points([tp])
        assert deduped == []
        assert any("fingerprint" in i for i in issues)


# ============================================================
# 覆盖率报告
# ============================================================


class TestCoverageReport:
    def test_full_coverage(self):
        items = [_make_item(f"01ARZ3NDEKTSV4RRFFQ69G5F{i:02d}") for i in range(3)]
        points = [
            TestPoint(
                version_id=VERSION_ID,
                item_ids=[it.id],
                module="登录",
                subcategory="s",
                title=f"t{i}",
                description="d",
                dimension=TestDimension.FUNCTIONAL,
            )
            for i, it in enumerate(items)
        ]
        report = build_coverage_report(points, items)
        assert report.total_items == 3
        assert report.coverage_ratio == 1.0
        assert report.uncovered_item_ids == []

    def test_partial_coverage(self):
        items = [_make_item(f"01ARZ3NDEKTSV4RRFFQ69G5F{i:02d}") for i in range(4)]
        points = [
            TestPoint(
                version_id=VERSION_ID,
                item_ids=[items[0].id, items[1].id],
                module="登录",
                subcategory="s",
                title="t",
                description="d",
                dimension=TestDimension.FUNCTIONAL,
            )
        ]
        report = build_coverage_report(points, items)
        assert report.coverage_ratio == 0.5
        assert set(report.uncovered_item_ids) == {items[2].id, items[3].id}

    def test_empty_items(self):
        report = build_coverage_report([], [])
        assert report.total_items == 0
        assert report.coverage_ratio == 0.0


# ============================================================
# 语义相似 warning
# ============================================================


class TestSemanticWarning:
    def test_warns_when_same_title_different_scope(self):
        tp_a = TestPoint(
            version_id=VERSION_ID,
            item_ids=["01ARZ3NDEKTSV4RRFFQ69G5FAV"],
            module="登录",
            subcategory="边界",
            title="验证码有效期",
            description="d",
            dimension=TestDimension.BOUNDARY,
            generation_scope=GenerationScope.ITEM,
            fingerprint="tp_a",
        )
        tp_b = TestPoint(
            version_id=VERSION_ID,
            item_ids=["01ARZ3NDEKTSV4RRFFQ69G5FAV", "01ARZ3NDEKTSV4RRFFQ69G5FB0"],
            module="登录",
            subcategory="边界",
            title="验证码有效期",
            description="d",
            dimension=TestDimension.BOUNDARY,
            generation_scope=GenerationScope.CROSS_ITEM,
            fingerprint="tp_b",
        )
        warnings = warn_semantic_duplicates([tp_a, tp_b])
        assert len(warnings) == 1
        assert "item" in warnings[0] and "cross_item" in warnings[0]

    def test_no_warning_when_unique(self):
        tp1 = TestPoint(
            version_id=VERSION_ID,
            item_ids=["01ARZ3NDEKTSV4RRFFQ69G5FAV"],
            module="登录",
            subcategory="s1",
            title="t1",
            description="d",
            dimension=TestDimension.FUNCTIONAL,
            fingerprint="tp_1",
        )
        tp2 = TestPoint(
            version_id=VERSION_ID,
            item_ids=["01ARZ3NDEKTSV4RRFFQ69G5FB0"],
            module="登录",
            subcategory="s2",
            title="t2",
            description="d",
            dimension=TestDimension.FUNCTIONAL,
            fingerprint="tp_2",
        )
        assert warn_semantic_duplicates([tp1, tp2]) == []
