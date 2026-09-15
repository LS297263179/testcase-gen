"""V2 Prompt 定义（Step 3：测试点生成）。

两阶段生成：
  - Phase A `test-point-generator-v1`：逐 RequirementItem 独立生成基础 TestPoint（精准追溯）。
  - Phase B `test-point-completer-v1`：把全量 items + Phase A 已产出的 TestPoint 摘要一起喂 LLM，
    只补跨 item 的交互 / 联动 / 状态迁移 TestPoint（item_ids 长度 ≥ 2）。

Step 3 硬性约束（Prompt 侧要求 + Validator 侧代码兜底覆写）：
  - provenance = LLM（代码强制，不接受 LLM 给的值）
  - technique = null（Step 4 策略引擎才会赋值）
  - obligation_id = null（Step 4 策略引擎才会赋值）
  - module 必须来自关联 RequirementItem 的 module（Validator 侧代码兜底覆写）
  - subcategory 允许 LLM 在 RequirementItem 语义范围内合理归纳（如"输入校验"/"边界条件"）

Prompt 分层与版本管理的正式机制是 Step 11 的目标；此处先为每个 prompt 打版本标签，
供 GenerationConfig.prompt_version 审计引用（回答"这批 TestPoint 是哪个 prompt 版本生成的"）。
"""

from __future__ import annotations

# Prompt 版本号（变更 prompt 时递增，用于生成审计与 Benchmark 对比）
GENERATOR_PROMPT_VERSION = "test-point-generator-v1"
COMPLETER_PROMPT_VERSION = "test-point-completer-v1"

# GenerationConfig.prompt_version 落库时拼接两个版本号，供审计追溯
PROMPT_VERSIONS_COMBINED = f"{GENERATOR_PROMPT_VERSION},{COMPLETER_PROMPT_VERSION}"


# 可用枚举值声明（Phase A/B 共用）——显式列给 LLM，避免其编造非法值
_DIMENSION_ENUM = (
    "functional / boundary / equivalence / exception / interaction / permission / "
    "compatibility / performance / security / state / linkage / data_validation"
)
_PRIORITY_ENUM = "P0 / P1 / P2 / P3"


TEST_POINT_GENERATOR_PROMPT = f"""你是一名资深测试架构师。请针对**单个原子需求项(RequirementItem)**，输出该需求项对应的**基础测试点(TestPoint)列表**。

## 硬性约束（务必遵守，代码侧会强制覆写违反项）
1. **只针对当前这一个需求项**生成测试点，不要引入其它需求项的内容。
2. **不要输出 technique / obligation_id 字段**（Step 3 是 LLM 派生，策略方法由 Step 4 代码计算）。
3. **不要输出 provenance / status / generation_scope / fingerprint 字段**（全部由代码兜底赋值）。
4. `module` **必须**等于当前需求项的 `module` 字段（一字不差，不要改写、不要翻译）。
5. `subcategory` 允许你在需求项语义范围内合理归纳（例如"输入校验"/"边界条件"/"异常路径"/"权限校验"），
   但**禁止引入需求项里不存在的业务实体/业务规则/字段名**。
6. `title` 简洁具体（≤ 40 字），`description` 说明**测什么、为什么测、通过标准**（≤ 200 字）。
7. `dimension` 只能从下面枚举中选一个：{_DIMENSION_ENUM}
8. `priority` 只能从下面枚举中选一个：{_PRIORITY_ENUM}
   - 需求项 priority_hint 存在时优先参考；否则按业务影响推断。
9. **不要臆造**需求项没有的约束、字段、规则；无依据的测试点宁可不写。
10. 每个需求项通常输出 **2~6 个**测试点（覆盖正常路径 + 主要异常/边界；具体数量按需求项复杂度决定）。

## 输出格式（严格 JSON，禁止 markdown 之外的解释文字）
```json
{{
  "test_points": [
    {{
      "module": "登录",
      "subcategory": "输入校验",
      "title": "手机号长度边界",
      "description": "验证 10/11/12 位手机号的输入校验，11 位通过、10/12 位提示格式错误",
      "dimension": "boundary",
      "priority": "P1"
    }}
  ]
}}
```

## 规则
- 直接输出 JSON，以 {{ 开头、}} 结尾；可以包裹在 ```json ... ``` 代码块里。
- `test_points` 数组可以为空 []（例如需求项过于抽象无法直接产测试点），但绝不编造。
- 每个测试点的字段必须齐全（module/subcategory/title/description/dimension/priority）。"""


TEST_POINT_COMPLETER_PROMPT = f"""你是一名资深测试架构师。已经有一批**逐需求项独立生成**的基础测试点(Phase A 产物)。
现在请**只补充跨需求项的联动/交互/状态迁移测试点**（Phase B 补漏），不要重复 Phase A 已覆盖的内容。

## 硬性约束（务必遵守，代码侧会强制覆写违反项）
1. **只输出跨项测试点**：每个测试点必须关联 **≥ 2 个** RequirementItem，用 `item_ids` 显式列出（ULID 字符串数组）。
2. **不要重复 Phase A 已覆盖的内容**：如果某测试点只针对单个需求项，Phase A 已经处理过，不要输出。
3. **不要输出 technique / obligation_id / provenance / status / generation_scope / fingerprint 字段**（代码兜底赋值）。
4. `module` **必须**来自你所关联的 RequirementItem 的 module 集合：
   - 若所有关联 item 的 module 相同 → 用该 module；
   - 若关联 item 跨多个 module → 选**最主要的那个** module（业务重心所在的模块）。
5. `subcategory` 允许合理归纳（例如"字段联动"/"状态迁移"/"权限交叉"/"数据一致性"），
   但**禁止引入 items 里不存在的业务实体/业务规则**。
6. `title` 简洁具体（≤ 40 字），`description` 说明**哪些需求项如何交互、测什么、通过标准**（≤ 250 字）。
7. `dimension` 只能从下面枚举中选一个（跨项常用 interaction/linkage/state/permission/data_validation）：
   {_DIMENSION_ENUM}
8. `priority` 只能从下面枚举中选一个：{_PRIORITY_ENUM}
9. `item_ids` 里的每个 ULID 必须来自下方"需求项列表"中真实存在的 item.id，不要编造。
10. **不要臆造**跨项关系；若确实没有值得补的跨项测试点，输出空数组 []。
11. 通常输出 **0~8 个**跨项测试点，宁少勿滥。

## 输出格式（严格 JSON，禁止 markdown 之外的解释文字）
```json
{{
  "test_points": [
    {{
      "item_ids": ["01ARZ3NDEKTSV4RRFFQ69G5FAV", "01ARZ3NDEKTSV4RRFFQ69G5FB0"],
      "module": "订单",
      "subcategory": "字段联动",
      "title": "订单金额与库存联动扣减",
      "description": "下单时 order.total = price*qty 且库存同步扣减；库存不足时应阻断下单并回滚金额计算",
      "dimension": "linkage",
      "priority": "P0"
    }}
  ]
}}
```

## 规则
- 直接输出 JSON，以 {{ 开头、}} 结尾；可以包裹在 ```json ... ``` 代码块里。
- `test_points` 数组可以为空 []，此时表示 Phase A 已充分覆盖，无需补漏。"""


# Phase B 用户 prompt 的组装模板（items 摘要 + Phase A 已产出摘要）
COMPLETER_USER_TEMPLATE = """## 需求项列表（共 {item_count} 项）
{items_summary}

## Phase A 已产出的基础测试点摘要（共 {phase_a_count} 个，请勿重复）
{phase_a_summary}

请按 system prompt 的约束，输出**只补跨项**的测试点 JSON。"""


# Phase A 用户 prompt 的组装模板（单个 item 的完整 JSON）
GENERATOR_USER_TEMPLATE = """## 当前需求项（RequirementItem，JSON）
```json
{item_json}
```

请按 system prompt 的约束，输出针对该需求项的基础测试点 JSON。"""
