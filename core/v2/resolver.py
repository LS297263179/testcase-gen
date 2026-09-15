"""多态目标解析 + 引用完整性校验（P1-5）。

问题：(target_type, target_id) 是 Polymorphic Association，SQLite 外键无法约束
"target_type=testcase 时 target_id 必须存在于 test_cases"。
方案：DB 不约束 → Domain 层约束。所有写入多态引用的路径必须先过 ReferentialValidator。

见 docs/v2/step1-data-model.md §6。
"""

from __future__ import annotations

from core.schemas import TargetType
from core.v2.db import v2_read_conn

# target_type → 表名（固定白名单，非用户输入，故表名拼接安全）
_TARGET_TABLES: dict[TargetType, str] = {
    TargetType.TESTCASE: "test_cases",
    TargetType.TESTPOINT: "test_points",
    TargetType.OBLIGATION: "coverage_obligations",
    TargetType.REQUIREMENT_ITEM: "requirement_items",
}


class ReferentialIntegrityError(Exception):
    """多态引用指向不存在的实体"""


class TargetResolver:
    """将 target_type 解析到具体表，并判断 target_id 是否存在"""

    def table_for(self, target_type: TargetType | str) -> str:
        tt = TargetType(target_type)
        if tt not in _TARGET_TABLES:
            raise ValueError(f"未知或不可解析的 target_type: {target_type}")
        return _TARGET_TABLES[tt]

    def exists(self, target_type: TargetType | str, target_id: str) -> bool:
        table = self.table_for(target_type)  # 白名单表名，安全拼接
        with v2_read_conn() as conn:
            row = conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (target_id,)).fetchone()
        return row is not None


class ReferentialValidator:
    """写入多态引用（ReviewFinding.target / obligation_coverage.target）前的强制校验"""

    def __init__(self, resolver: TargetResolver | None = None) -> None:
        self.resolver = resolver or TargetResolver()

    def validate(self, target_type: TargetType | str, target_id: str) -> None:
        """target 不存在则抛 ReferentialIntegrityError"""
        if not self.resolver.exists(target_type, target_id):
            tt = TargetType(target_type).value
            raise ReferentialIntegrityError(f"{tt} {target_id} 不存在，拒绝写入多态引用")
