"""V2 业务确定性指纹 - TestPoint / TestCase 幂等身份计算。

Step 3 引入：TestPoint 的 ULID 是每次新生成的，无法用于跨轮次的幂等识别。
业务身份由 (version_id, generation_scope, sorted(item_ids), module, subcategory, normalize(title))
六元组唯一确定 → sha256 得到 fingerprint，DB 层加 UNIQUE 索引，Repository 层按 fingerprint upsert。

Step 5 引入 TestCase 的双指纹（身份/内容分离）：
  - fingerprint（身份）：基于 TestPoint 来源身份（version_id | generation_mode | sorted(test_point_ids)），
    不依赖 title/steps 等可编辑内容 → 人工修改 title 不改变身份（Step 9 Revision 的基础）。
  - content_hash（内容）：基于 title/precondition/steps/expected/type/priority，
    用于判断内容是否变化（Step 9 Revision / Preference Learning 数据源）。

不放在 Schema 层的原因：Schema 保持纯声明，指纹算法属于持久化/领域逻辑。
"""

from __future__ import annotations

import hashlib
import json
import re

# 归一化时压缩的空白字符（含全角空格）
_WS_RE = re.compile(r"[\s\u3000]+")


def normalize_text(text: str | None) -> str:
    """文本归一化：strip + 内部空白压缩为单空格 + 小写化。

    用于 fingerprint 计算与去重键，避免因大小写/空白差异导致同一业务身份产生多个指纹。
    不做标点归一（不同标点在测试点标题里可能表达不同语义，保守处理）。
    """
    if not text:
        return ""
    return _WS_RE.sub(" ", text.strip()).lower()


def compute_testpoint_fingerprint(
    *,
    version_id: str | None,
    generation_scope: str,
    item_ids: list[str],
    module: str,
    subcategory: str,
    title: str,
) -> str:
    """计算 TestPoint 的业务确定性指纹（32 位十六进制，前缀 tp_）。

    六元组构成业务身份：
      - version_id：不同 RequirementVersion 的 TestPoint 天然隔离
      - generation_scope：Phase A（item）与 Phase B（cross_item）分开算，避免同一 item 下
        Phase A 单点与 Phase B 跨点因 title 巧合而碰撞
      - sorted(item_ids)：M:N 关联集合，排序后拼接保证顺序无关
      - module / subcategory / normalize(title)：内容三元组

    返回格式：tp_<sha256 前 32 位十六进制>，共 35 字符。
    """
    payload = "|".join(
        [
            version_id or "",
            generation_scope or "",
            ",".join(sorted(item_ids or [])),
            normalize_text(module),
            normalize_text(subcategory),
            normalize_text(title),
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"tp_{digest}"


def canonical_strategy_params(params: dict | None) -> str:
    """将 strategy_params 规范化为确定性字符串（用于 fingerprint 计算）。

    - sort_keys=True：保证字典序一致，不受插入顺序影响
    - ensure_ascii=False：中文不转义，减少长度
    - separators=(',', ':')：去除多余空格，紧凑格式
    - None 或空 dict 统一返回 "{}"
    """
    if not params:
        return "{}"
    return json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def compute_strategy_testpoint_fingerprint(
    *,
    version_id: str | None,
    obligation_id: str,
    technique: str,
    strategy_params: dict | None,
) -> str:
    """计算 Step 4 策略引擎派生 TestPoint 的业务确定性指纹（32 位十六进制，前缀 tp_）。

    与 Step 3 LLM 派生的指纹公式不同：策略 TestPoint 的业务身份由
    “覆盖哪个 obligation + 用什么技术 + 结构化参数”决定，而非 title/module/subcategory。
    同一 obligation 派生的多个 TestPoint（1:N）靠 strategy_params 区分，
    避免仅靠 title 区分不够可靠的问题。

    四元组构成业务身份：
      - version_id：不同 RequirementVersion 天然隔离
      - obligation_id：同一 obligation 派生的多个 TestPoint 靠 strategy_params 区分
      - technique：边界值/等价类/权限矩阵等技术分开算
      - canonical(strategy_params)：结构化参数的确定性序列化
        示例：{"boundary_type":"min_minus_1","value":0} / {"class":"valid_enum_value","value":"active"}

    返回格式：tp_<sha256 前 32 位十六进制>，共 35 字符（与 Step 3 公式保持一致的长度与前缀）。
    """
    payload = "|".join(
        [
            version_id or "",
            "strategy",  # 固定 generation_scope，与 Step 3 公式区分
            obligation_id or "",
            technique or "",
            canonical_strategy_params(strategy_params),
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"tp_{digest}"


def compute_testcase_fingerprint(
    *,
    version_id: str | None,
    generation_mode: str,
    test_point_ids: list[str],
) -> str:
    """计算 TestCase 的身份指纹（32 位十六进制，前缀 tc_）。

    ★ 身份/内容分离设计（Step 5 核心决策）：
    fingerprint 标识“这是哪一个 TestCase”（身份），而非“当前文本长什么样”（内容）。
    公式只含来源身份三元组，不含 title/precondition/steps 等可编辑内容：
      - version_id：不同 RequirementVersion 天然隔离
      - generation_mode：code/llm/hybrid 三种生成方式视为不同身份
        （code 生成的模板用例与 llm 生成的自然语言用例业务身份不同）
      - sorted(test_point_ids)：1:1 派生下单元素，身份天然稳定；排序保证顺序无关

    不含 run_id：同 version 跨 run 幂等复用旧 ULID（对齐 Step 3 D5 决策）。
    人工修改 title 后 fingerprint 不变 → 同一 TestCase 不同 revision（Step 9 基础）。

    返回格式：tc_<sha256 前 32 位十六进制>，共 35 字符。
    """
    payload = "|".join(
        [
            version_id or "",
            generation_mode or "",
            ",".join(sorted(test_point_ids or [])),
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"tc_{digest}"


def compute_testcase_content_hash(
    *,
    title: str,
    precondition: str,
    steps_text: str,
    expected: str,
    type: str,
    priority: str,
) -> str:
    """计算 TestCase 的内容哈希（32 位十六进制，前缀 tch_）。

    与 fingerprint（身份）分离：content_hash 追踪“内容是否变化”。
    人工修改 title/steps/expected 后 content_hash 变，但 fingerprint（身份）不变。
    用途：Step 9 Revision / Preference Learning 据此判断用例内容是否被修改。

    steps_text 由 TestCase.render_steps_text() 序列化后传入（保持本函数与模型解耦）。

    返回格式：tch_<sha256 前 32 位十六进制>，共 36 字符。
    """
    payload = "|".join(
        [
            normalize_text(title),
            normalize_text(precondition),
            normalize_text(steps_text),
            normalize_text(expected),
            type or "",
            priority or "",
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"tch_{digest}"
