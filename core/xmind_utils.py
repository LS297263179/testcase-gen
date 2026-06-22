"""XMind 文件解析与模板生成"""

import json
import zipfile
import os
import io


def parse_xmind(filepath: str) -> list[dict]:
    """解析 XMind 文件，返回思维导图结构列表（每个 sheet 一个）。

    返回格式：
    [
        {
            "title": "Sheet1",
            "root": {
                "title": "根节点",
                "children": [
                    {"title": "子节点1", "children": [...]},
                    {"title": "子节点2", "children": []}
                ]
            }
        }
    ]
    """
    with zipfile.ZipFile(filepath, "r") as zf:
        names = zf.namelist()
        # XMind 8+ 格式：content.json
        if "content.json" in names:
            data = json.loads(zf.read("content.json"))
            return _parse_json_format(data)
        # 旧版格式：content.xml
        if "content.xml" in names:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(zf.read("content.xml"))
            return _parse_xml_format(root)
    raise ValueError("无法识别的 XMind 文件格式")


def _parse_json_format(data: list) -> list[dict]:
    """解析 XMind 8+ JSON 格式"""
    sheets = []
    for sheet in data:
        title = sheet.get("title", "Sheet")
        root_topic = sheet.get("rootTopic")
        if not root_topic:
            continue
        sheets.append({
            "title": title,
            "root": _extract_topic_json(root_topic),
        })
    return sheets


def _extract_topic_json(topic: dict) -> dict:
    """递归提取 JSON 格式的节点"""
    result = {"title": topic.get("title", "")}
    children = topic.get("children", {})
    # children 可能是 {"attached": [...]} 结构
    if isinstance(children, dict):
        attached = children.get("attached", [])
    elif isinstance(children, list):
        attached = children
    else:
        attached = []
    if attached:
        result["children"] = [_extract_topic_json(c) for c in attached]
    return result


def _parse_xml_format(root) -> list[dict]:
    """解析旧版 XMind XML 格式"""
    ns = {"x": "xmind/namespace"}
    sheets = []
    for sheet in root.findall(".//x:sheet", ns):
        title_el = sheet.find("x:title", ns)
        title = title_el.text if title_el is not None else "Sheet"
        topic_el = sheet.find("x:topic", ns)
        if topic_el is None:
            continue
        sheets.append({
            "title": title,
            "root": _extract_topic_xml(topic_el, ns),
        })
    return sheets


def _extract_topic_xml(topic_el, ns) -> dict:
    """递归提取 XML 格式的节点"""
    title_el = topic_el.find("x:title", ns)
    result = {"title": title_el.text if title_el is not None else ""}
    children_el = topic_el.find("x:children", ns)
    if children_el is not None:
        topics_el = children_el.find("x:topics", ns)
        if topics_el is not None:
            attached = topics_el.findall("x:topic", ns)
            if attached:
                result["children"] = [_extract_topic_xml(c, ns) for c in attached]
    return result


def flatten_topics(node: dict, path: str = "") -> list[dict]:
    """将树形结构展平为列表，保留路径信息。

    返回：[{"path": "模块 > 子模块", "title": "节点名", "depth": 2}, ...]
    """
    items = []
    current_path = f"{path} > {node['title']}" if path else node["title"]
    items.append({
        "path": current_path,
        "title": node["title"],
        "depth": current_path.count(">") + 1,
    })
    for child in node.get("children", []):
        items.extend(flatten_topics(child, current_path))
    return items


def _make_case(id: str, title: str, steps: str, expected: str) -> dict:
    """快速构造一个带预期结果的测试用例节点（优先级由 AI 自动判断）"""
    return {
        "id": id,
        "class": "topic",
        "title": title,
        "children": {
            "attached": [
                {"id": f"{id}_s", "class": "topic", "title": f"步骤：{steps}"},
                {"id": f"{id}_e", "class": "topic", "title": f"预期结果：{expected}"},
            ]
        },
    }


def generate_template(output_path: str) -> str:
    """生成示例 XMind 模板文件，返回文件路径。

    模板结构说明（4层）：
      项目 → 模块 → 测试场景 → 测试用例
    每个测试用例下包含2个子节点：步骤、预期结果（优先级由 AI 自动判断）。
    """
    content = [
        {
            "id": "sheet_1",
            "class": "sheet",
            "title": "测试用例模板",
            "rootTopic": {
                "id": "root",
                "class": "topic",
                "title": "项目名称",
                "structureClass": "org.xmind.ui.map.unbalanced",
                "children": {
                    "attached": [
                        {
                            "id": "demo",
                            "class": "topic",
                            "title": "【模板使用说明 - 可删除】",
                            "children": {
                                "attached": [
                                    {
                                        "id": "demo_desc",
                                        "class": "topic",
                                        "title": "本模板共4层结构，每层对应Excel字段如下",
                                    },
                                    {
                                        "id": "demo_l1",
                                        "class": "topic",
                                        "title": "第1层：项目/产品名称（根节点）",
                                        "children": {
                                            "attached": [
                                                {"id": "demo_l1_ex", "class": "topic", "title": "示例：XX管理系统"},
                                            ]
                                        },
                                    },
                                    {
                                        "id": "demo_l2",
                                        "class": "topic",
                                        "title": "第2层：功能模块 → Excel【module】字段",
                                        "children": {
                                            "attached": [
                                                {"id": "demo_l2_ex", "class": "topic", "title": "示例：登录模块、用户管理、订单管理"},
                                            ]
                                        },
                                    },
                                    {
                                        "id": "demo_l3",
                                        "class": "topic",
                                        "title": "第3层：测试场景/功能点（用于归类用例）",
                                        "children": {
                                            "attached": [
                                                {"id": "demo_l3_ex", "class": "topic", "title": "示例：正常登录、异常登录、密码找回"},
                                            ]
                                        },
                                    },
                                    {
                                        "id": "demo_l4",
                                        "class": "topic",
                                        "title": "第4层：用例标题 → Excel【title】字段",
                                        "children": {
                                            "attached": [
                                                {"id": "demo_l4_ex", "class": "topic", "title": "示例：用户名密码登录成功"},
                                            ]
                                        },
                                    },
                                    {
                                        "id": "demo_fields",
                                        "class": "topic",
                                        "title": "第5层：用例详情（每个用例下挂2个子节点）",
                                        "children": {
                                            "attached": [
                                                {"id": "demo_f1", "class": "topic", "title": "步骤：xxx → Excel【steps】字段，操作步骤用换行分隔"},
                                                {"id": "demo_f2", "class": "topic", "title": "预期结果：xxx → Excel【expected】字段"},
                                            ]
                                        },
                                    },
                                    {
                                        "id": "demo_ai",
                                        "class": "topic",
                                        "title": "以下字段由 AI 自动生成，无需在模板中填写",
                                        "children": {
                                            "attached": [
                                                {"id": "demo_a1", "class": "topic", "title": "priority：优先级（P0=阻塞/P1=严重/P2=一般/P3=轻微）"},
                                                {"id": "demo_a2", "class": "topic", "title": "precondition：前置条件"},
                                                {"id": "demo_a3", "class": "topic", "title": "remark：备注"},
                                                {"id": "demo_a4", "class": "topic", "title": "id：用例编号（系统自动生成）"},
                                            ]
                                        },
                                    },
                                ]
                            },
                        },
                        {
                            "id": "t1",
                            "class": "topic",
                            "title": "登录模块",
                            "children": {
                                "attached": [
                                    {
                                        "id": "t1_1",
                                        "class": "topic",
                                        "title": "正常登录",
                                        "children": {
                                            "attached": [
                                                _make_case("t1_1_1", "用户名密码登录", "输入正确用户名和密码，点击登录按钮", "跳转到首页"),
                                                _make_case("t1_1_2", "手机号验证码登录", "输入手机号，获取验证码并填写，点击登录", "跳转到首页"),
                                                _make_case("t1_1_3", "记住我功能", "勾选'记住我'后登录，关闭浏览器重新打开", "自动登录，无需再次输入"),
                                            ]
                                        },
                                    },
                                    {
                                        "id": "t1_2",
                                        "class": "topic",
                                        "title": "异常登录",
                                        "children": {
                                            "attached": [
                                                _make_case("t1_2_1", "密码错误", "输入正确用户名和错误密码，点击登录", "提示'用户名或密码错误'"),
                                                _make_case("t1_2_2", "账号不存在", "输入不存在的用户名，点击登录", "提示'账号不存在'"),
                                                _make_case("t1_2_3", "账号锁定", "连续5次输入错误密码", "账号锁定30分钟，提示'账号已锁定'"),
                                            ]
                                        },
                                    },
                                ]
                            },
                        },
                        {
                            "id": "t2",
                            "class": "topic",
                            "title": "首页模块",
                            "children": {
                                "attached": [
                                    {
                                        "id": "t2_1",
                                        "class": "topic",
                                        "title": "数据展示",
                                        "children": {
                                            "attached": [
                                                _make_case("t2_1_1", "统计数据加载", "登录后进入首页", "统计数据正确显示，无加载错误"),
                                                _make_case("t2_1_2", "数据刷新", "点击刷新按钮", "数据实时更新，显示最新状态"),
                                            ]
                                        },
                                    },
                                    {
                                        "id": "t2_2",
                                        "class": "topic",
                                        "title": "导航跳转",
                                        "children": {
                                            "attached": [
                                                _make_case("t2_2_1", "菜单跳转", "点击左侧菜单项", "正确跳转到对应页面，URL正确"),
                                            ]
                                        },
                                    },
                                ]
                            },
                        },
                        {
                            "id": "t3",
                            "class": "topic",
                            "title": "用户管理模块",
                            "children": {
                                "attached": [
                                    {
                                        "id": "t3_1",
                                        "class": "topic",
                                        "title": "新增用户",
                                        "children": {
                                            "attached": [
                                                _make_case("t3_1_1", "正常新增用户", "填写所有必填项，点击保存", "用户创建成功，列表中显示新用户"),
                                                _make_case("t3_1_2", "用户名重复", "填写已存在的用户名，点击保存", "提示'用户名已存在'"),
                                            ]
                                        },
                                    },
                                    {
                                        "id": "t3_2",
                                        "class": "topic",
                                        "title": "编辑用户",
                                        "children": {
                                            "attached": [
                                                _make_case("t3_2_1", "修改用户信息", "修改用户姓名，点击保存", "更新成功，列表显示新姓名"),
                                            ]
                                        },
                                    },
                                    {
                                        "id": "t3_3",
                                        "class": "topic",
                                        "title": "删除用户",
                                        "children": {
                                            "attached": [
                                                _make_case("t3_3_1", "删除确认", "点击删除按钮，在确认弹框中点击确定", "用户被移除，列表中不再显示"),
                                            ]
                                        },
                                    },
                                ]
                            },
                        },
                    ]
                },
            },
        }
    ]

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    manifest = {
        "file-entries": {
            "content.json": {},
            "metadata.json": {},
        }
    }
    metadata = {
        "creator": {"name": "testcase-gen", "version": "1.0.0"}
    }

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.json", json.dumps(content, ensure_ascii=False, indent=2))
        zf.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False))
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
    return output_path
