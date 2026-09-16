"""V2 Prompt 定义（Step 5：测试用例合成 + 复杂正则测试数据）。

两个 Prompt：
  - `test-case-synthesizer-v1`：消费 LLM TestPoint + RequirementItem context + DataPlan，
    合成完整 TestCase（steps / expected / precondition / type / priority）。
  - `test-data-pattern-v1`：为复杂正则生成「匹配 / 不匹配」样例（LLM 兜底，代码 re.fullmatch 验证）。

Step 5 硬性约束（Prompt 侧要求 + Validator 侧代码兜底覆写）：
  - 不输出 id / run_id / display_id / fingerprint / provenance / status / generation_mode（代码赋值）
  - module 来自 TestPoint（代码覆写）；test_point_ids 由代码按 1:1 注入
  - type 只能从 TestCaseType 枚举选；priority 只能从 P0-P3 选（代码兜底）
  - steps 为有序数组，seq 由代码重排（LLM 只需给出顺序）

★ 核心原则：LLM 可以生成测试数据/步骤，但不能自证正确——复杂正则样例必须过代码 re.fullmatch 验证。
Prompt 分层与版本管理的正式机制是 Step 11 的目标；此处先打版本标签供 GenerationConfig.prompt_version 审计。
"""

from __future__ import annotations

# Prompt 版本号（变更 prompt 时递增，用于生成审计与 Benchmark 对比）
SYNTHESIZER_PROMPT_VERSION = "test-case-synthesizer-v1"
PATTERN_DATA_PROMPT_VERSION = "test-data-pattern-v1"

# GenerationConfig.prompt_version 落库时拼接，供审计追溯
PROMPT_VERSIONS_COMBINED = f"{SYNTHESIZER_PROMPT_VERSION},{PATTERN_DATA_PROMPT_VERSION}"


# 可用枚举值声明——显式列给 LLM，避免其编造非法值
_TYPE_ENUM = (
    "functional / boundary / equivalence / exception / permission / "
    "compatibility / performance / security / state / linkage"
)
_PRIORITY_ENUM = "P0 / P1 / P2 / P3"


TEST_CASE_SYNTHESIZER_PROMPT = f"""你是一名资深测试工程师。请把一个**测试点(TestPoint)**合成为一条**可执行的测试用例(TestCase)**。

## 输入说明
- 测试点：包含 module / title / description / dimension / priority（测什么、为什么测）。
- 需求项上下文：该测试点关联的原子需求项（字段规格 / 业务规则 / 验收标准）。
- 测试数据计划(DataPlan)：代码已为字段级约束准备好的具体测试数据（可能为空）；
  **若 DataPlan 提供了数据，steps 里必须使用这些数据**，不要自行编造不同的值。

## 硬性约束（务必遵守，代码侧会强制覆写违反项）
1. **不要输出** id / run_id / display_id / fingerprint / provenance / status / generation_mode / module / test_point_ids 字段（全部由代码赋值/覆写）。
2. `title` 简洁具体（≤ 50 字），描述"验证什么场景 + 预期结果"。
3. `precondition` 说明执行前置条件（如"用户已登录"、"系统中已存在一条订单"）；无则给空字符串。
4. `steps` 是**有序数组**，每步含：
   - `action`：具体操作（必填，非空），如"在手机号输入框输入 13800138000"
   - `data`：该步涉及的测试数据（可选）
   - `expected`：该步的预期结果（可选）
   步骤要可被人工或自动化执行，粒度适中（通常 2~6 步）。
5. `expected` 是**整条用例的最终预期结果**（必填，非空），与测试点的验证目标一致。
6. `type` 只能从下面枚举选一个：{_TYPE_ENUM}
   - 参考测试点的 dimension：boundary→boundary、equivalence→equivalence、permission→permission、
     exception→exception、interaction/linkage→linkage、state→state、security→security、其余→functional。
7. `priority` 只能从下面枚举选一个：{_PRIORITY_ENUM}（优先沿用测试点的 priority）。
8. **不要臆造**需求项/测试点里没有的字段、规则、约束。
9. `remark` 可选，补充说明（如"需人工确认负值合理性"）；无则给空字符串。

## 输出格式（严格 JSON，禁止 markdown 之外的解释文字）
```json
{{
  "title": "手机号为空时无法登录",
  "precondition": "用户在登录页面",
  "steps": [
    {{"action": "不输入手机号", "data": "", "expected": "手机号输入框为空"}},
    {{"action": "输入密码并点击登录", "data": "password123", "expected": "系统阻止提交"}}
  ],
  "expected": "系统提示'请输入手机号'，登录被阻止",
  "type": "functional",
  "priority": "P1",
  "remark": ""
}}
```

## 规则
- 直接输出 JSON，以 {{ 开头、}} 结尾；可以包裹在 ```json ... ``` 代码块里。
- steps 数组**不可为空**（至少 1 步）；title / expected 不可为空。"""


TEST_DATA_PATTERN_PROMPT = """你是一名测试数据构造专家。请为给定的**正则表达式**生成一个测试样例。

## 任务
根据要求生成「匹配」或「不匹配」该正则的字符串样例。

## 硬性约束
1. 若要求"匹配"：你给的样例必须能被 Python `re.fullmatch(pattern, sample)` **完全匹配**（整个字符串匹配，非部分匹配）。
2. 若要求"不匹配"：你给的样例必须**不能**被 `re.fullmatch(pattern, sample)` 匹配。
3. 样例要贴近业务语义（如手机号正则给真实号段、邮箱正则给常见格式），不要用无意义乱码。
4. **代码会用 re.fullmatch 验证你的样例**，验证不通过会要求重试，请务必确保正确。

## 输出格式（严格 JSON）
```json
{"sample": "你生成的样例字符串"}
```

## 规则
- 只输出 JSON，以 { 开头、} 结尾。
- sample 字段必填，为字符串（不匹配场景可以是空字符串或明显非法值）。"""


# 合成用例的 user prompt 模板
SYNTHESIZER_USER_TEMPLATE = """## 测试点（TestPoint，JSON）
```json
{testpoint_json}
```

## 关联需求项上下文（RequirementItem，JSON）
```json
{item_json}
```

## 测试数据计划（DataPlan，代码已准备好的具体测试数据；为空表示无字段级数据约束）
```json
{data_plan_json}
```

请按 system prompt 的约束，输出该测试点对应的**一条**测试用例 JSON。"""


# 复杂正则数据生成的 user prompt 模板
PATTERN_DATA_USER_TEMPLATE = """## 正则表达式（pattern）
```
{pattern}
```

## 字段上下文
- 字段名：{field_name}
- 字段含义：{field_label}

## 要求
请生成一个**{want}**该正则的字符串样例（{match_desc}）。

请按 system prompt 的约束，输出 JSON。"""
