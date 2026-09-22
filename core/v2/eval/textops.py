"""V2 Step 11 S7 评价层公共文本 helper（裁决 D8）。

存在理由：`rails.py`（Semantic Rail）与 `matching.py`（Identity Rail）都需要"锚点子串判定"与
"statement 相似度"两把尺子。原先它们分别是 `matching._anchor_satisfied` 与
`change_impact._similarity` 两个**私有**符号；跨模块依赖私有名会形成长期隐性耦合，
故按裁决 D8 抽出为公共 helper，且**只抽这两个通用文本函数，不扩展成额外 NLP 层**。

口径冻结（不得在此文件内演化语义）：
  - `anchor_satisfied`：归一化后必须是某个**结构化字段**（step.action / tc.expected）的组成部分；
    绝不扫描 title / precondition / remark 等自由文本（设计文档 §8）。
  - `statement_similarity`：normalize_text + difflib.SequenceMatcher.ratio，与
    `core.v2.change_impact._similarity` **同口径**；由 tests/test_benchmark_rails.py 的防漂移测试
    逐样本断言两者恒等，防止两处实现各自演化。
"""

from __future__ import annotations

import difflib

from core.v2.fingerprint import normalize_text


def anchor_satisfied(anchor: str, haystacks: list[str]) -> bool:
    """结构锚点判定：归一化后必须是某个结构化字段（step.action / tc.expected）的组成部分。

    空锚点恒为 False（"未声明"不等于"满足"）。只在传入的结构化字段内比对，
    调用方负责只传 step.action / tc.expected，不得传自由文本字段。
    """
    needle = normalize_text(anchor)
    if not needle:
        return False
    return any(needle in normalize_text(text) for text in haystacks if text)


def statement_similarity(a: str, b: str) -> float:
    """两条 statement 的归一化相似度 [0,1]（与 change_impact._similarity 同口径）。

    仅供 Identity Rail **诊断**（module/type/statement 一致性统计）与既有 CANDIDATE 判定使用；
    按设计文档 §4.1 规则 3，相似度**永不自动计分**，只产生 CANDIDATE / 辅助诊断。
    """
    return difflib.SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


__all__ = ["anchor_satisfied", "statement_similarity"]
