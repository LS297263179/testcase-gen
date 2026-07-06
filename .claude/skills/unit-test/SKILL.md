---
name: unit-test
description: "运行 pytest 测试套件 / 编写单元测试 / 保证代码质量。适合在修改代码后验证无破坏、或需要为新功能补充测试时使用"
argument-hint: "[test-file | --new | --cover | --last]"
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
user-invocable: true
---

# /unit-test

运行和编写测试，保证代码质量。

## 运行测试
```
/unit-test                    → 全部测试
/unit-test db                 → 数据库测试（test_db.py）
/unit-test web                → API 测试（test_web_api.py）
/unit-test gen                → 生成器测试（test_generator.py）
/unit-test regression         → 回归测试（test_regression.py）
/unit-test -k "关键词"         → 关键词过滤
/unit-test --cover            → 带覆盖率报告
/unit-test --last             → 重新运行上次失败的测试
```

## 编写测试
```
/unit-test --new core/xxx.py  → 为指定文件编写单元测试
/unit-test --add 函数名        → 为指定函数补充测试用例
```

## 测试组织
```
tests/
├── conftest.py           # 共享 fixtures（tmp_db, app, client, auth_client, mock_llm_client）
├── test_db.py            # 数据库层测试
├── test_generator.py     # 生成器核心测试（解析、去重、校验、裁剪）
├── test_web_api.py       # Web API 测试
└── test_regression.py    # 全功能回归测试
```

## 测试风格
- 使用 `pytest`，类组织 `class TestXxx`
- `tmp_db` fixture 测数据库，`client`/`auth_client` 测 API
- `mock_llm_client` 模拟 LLM 调用（不实际调 API）
- 断言用 `assert`，函数名 `test_*`

## 覆盖要求
1. **正常路径** — 核心功能按预期工作
2. **边界条件** — 空值、零值、超长输入、空列表
3. **异常路径** — 错误输入、不存在的记录、重复操作
4. **数据格式** — 新旧格式兼容、JSON 解析、控制字符
5. **权限隔离** — 用户 A 看不到用户 B 的数据
