"""V2 Prompt 定义（Step 2：需求解析）。

Prompt 分层与版本管理的正式机制是 Step 11 的目标；此处先为每个 prompt 打版本标签，
供 GenerationConfig.prompt_version 审计引用（回答"这批 IR 是哪个 prompt 版本抽的"）。
"""

from __future__ import annotations

# 需求解析器 Prompt 版本（变更 prompt 时递增，用于生成审计与 Benchmark 对比）
PARSER_PROMPT_VERSION = "requirement-parser-v1"

IR_EXTRACTION_PROMPT = """你是一名资深需求分析师 + 测试架构师。请把用户需求拆解为**结构化的原子需求项(IR)**，供下游测试策略引擎(代码)消费。

## 核心要求
1. **原子化**：每个需求项只表达一个独立、可单独验证的意图。
2. **字段级抽取**：凡涉及输入字段，必须抽取其类型与约束(长度/取值范围/格式/枚举/必填/可空/唯一)——下游据此用**代码**算边界值与等价类，务必抽全，不要遗漏数字/长度/格式限制。
3. **业务规则**：抽取公式/比较/范围/枚举类规则，并标注 expression_type（能代码求值的用 formula/comparison/range/enum，纯文字描述用 natural_language）。
4. **权限**：抽取"角色 × 资源 × 操作 → 允许/拒绝"，下游据此生成权限矩阵。
5. **置信度**：每个需求项给 confidence(0~1)。原文明确直述→高分(≥0.8)；含糊或需推断→低分(<0.6)。
6. **不要臆造**原文没有的需求；无法确定的约束留空(null)，不要编数字。
7. **溯源**：每个需求项尽量给 source_ref —— 摘录原文中对应的关键句/片段（原文照抄，不要改写），用于追溯到原始需求；确实无法定位时可留空字符串。

## 需求项类型 type（择一）
function / data_field / business_rule / constraint / interaction / permission / interface / non_functional

## 输出格式（严格 JSON，禁止额外文字）
{
  "items": [
    {
      "type": "data_field",
      "module": "模块名",
      "statement": "原子化需求描述",
      "fields": [
        {"name": "字段英文标识", "label": "展示名", "data_type": "string|int|float|bool|date|datetime|enum|email|phone|url|id_card",
         "required": true, "nullable": false, "min_length": null, "max_length": null,
         "min_value": null, "max_value": null, "pattern": null, "enum_values": [], "unique": false, "default": null, "unit": null}
      ],
      "rules": [{"name": "", "expression_type": "natural_language|formula|comparison|range|enum", "expression": "", "inputs": [], "expected": ""}],
      "permissions": [{"role": "", "resource": "", "action": "", "allowed": true, "condition": null}],
      "acceptance_criteria": [""],
      "source_ref": "原文中对应的关键句/片段摘录（追溯用，尽量提供，无法定位则留空字符串）",
      "priority_hint": "P0|P1|P2|P3",
      "confidence": 0.9
    }
  ]
}

## 规则
- 某类内容不存在时，对应数组留空 []，绝不编造。
- fields 里的约束(min/max/pattern/enum_values)只在原文有依据时填，否则 null/[]。
- confidence 必填。
- 直接输出 JSON，以 { 开头、} 结尾，不要 markdown 之外的解释。"""

# 分段抽取时，追加到 user prompt 的上下文提示（告知这是长需求的第 N 段）
SEGMENT_HINT = "（注意：这是一份较长需求的第 {idx}/{total} 段，请只针对本段内容抽取需求项，保持 JSON 格式一致。）"
