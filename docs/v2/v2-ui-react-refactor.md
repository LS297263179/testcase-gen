# V2 产品级 UI/UX 重构记录（React + TypeScript + Vite）

> 定位：Step 10 收尾后、经用户拍板启动的**产品级前端重构**交付记录（对应设计稿
> `v2-product-ux-redesign.md`，实施计划见批准文档）。本轮不新增后端领域模型，
> 不改变 Runtime / schema_version=10 / V1 任何文件。

## 1. 冻结的用户决策

1. V2 前端升级为 **React + TypeScript + Vite**（替代 Step 10.4 原生 JS MVP）；后端技术栈与 V1 零改动。
2. **Run-centric**：核心业务对象 = 测试运行（Run = 一次 Step 2→8 流水线执行），不新增
   Project / TestTask 实体，不建虚拟 Project 模型，schema_version 保持 10。
3. 材料/XMind 归位「资源」二级区：项目材料复用 V1 现有 `/api/materials`；
   **V2 生成时引用材料暂未接入**（UI 明确标注，不伪装）；`POST /api/v2/runs` 不加 material_ids。
4. 唯一后端补充：只读端点 `GET /api/v2/runs/<run_id>/requirements`（13→14 端点）。
5. 同步 Web Runtime MVP 限制不变：无异步任务 / SSE / 轮询伪进度。

## 2. 信息架构（落地形态）

```text
/v2（React SPA，HashRouter，认证沿用 V1 session + 302 跳回）
├── #/                工作台：进行中/最近运行 + 质量概览 + [新建测试运行]
├── #/runs            测试运行列表（MVP：本人最近 50 条）
├── #/runs/new        新建测试运行（同步等待 + 失败定位 + 材料暂未接入标注）
├── #/runs/:id        运行详情（二级 Tab，面包屑「测试运行 / <需求> / Tab」）
│     ├── 概览            Pipeline 阶段步骤条（counts/status 推导，不伪装饰时）+ 指标 + 下一步动作
│     ├── requirements      需求与 AI 分析（★新端点；FieldSpec/规则/权限展开、低置信、覆盖）
│     ├── test-points       测试点（LLM/STRATEGY 来源摘要 + 筛选）
│     ├── test-cases        测试用例（状态 chips + Drawer：编辑/乐观锁409/Revisions/Trace/重评审）
│     ├── review            AI 评审（6 维 + 加权/双指标明细 + findings 按严重度分组跳转）
│     ├── optimizer         优化（「发现→归档→保留」叙事；诚实标注 diff 不持久化）
│     └── report            报告（一页纸聚合 + 打印友好）
├── #/resources/materials   项目材料（复用 V1 /api/materials CRUD）
├── #/resources/xmind       XMind 与迁移（V1 能力定位说明 + CLI 迁移指引）
└── #/settings              系统设置（/api/v2/health + 模型配置复用 V1 /api/model-config；
                              API Key 不回显，仅 placeholder 脱敏提示）
```

## 3. 代码改动清单

| 类别 | 文件 | 说明 |
|---|---|---|
| 后端（唯一新增） | `web/v2_service.py` | `get_requirements_context(run_id)`（只读，复用 repo） |
| | `web/v2_routes.py` | `GET /api/v2/runs/<id>/requirements`（login_required，纳入 gating） |
| | `tests/test_v2_web_api.py` | +test_39~42（200/404/401/503 gating）；test_34 断言改指 SPA 壳 |
| 前端工程 | `frontend-v2/` | React18+TS+Vite，约 25 个源文件；`npm run build` 产物 `static/v2/v2_react.{js,css}`（固定文件名入库，部署无需 Node） |
| 模板 | `templates/v2.html` | 重写为 SPA 壳（Flask `/v2` 路由与 302 行为零改动） |
| 退役删除 | `static/v2_app.js`、`static/v2_style.css` | Step 10.4 原生 JS 前端，能力已全部由新前端承接 |
| 配置 | `.gitignore` / `.dockerignore` | 排除 node_modules/dist；构建产物入库 |

**零改动确认**：`core/`（全部）、`web/` V1 路由、`data.db`、V1 三件套（index.html/app.js/style.css）、
Runtime、状态机、schema_version=10。

## 4. 验收证据

- 全量 pytest：**984 passed + 3 skipped**（基线 980 + 新端点 4）；ruff check/format 双绿；health schema_version=10。
- 浏览器 E2E（播种账号 ui_reviewer，一次性脚本已删除）13 项走查全过：
  工作台→运行列表→详情 7 Tab→用例 Drawer→人工编辑保存（**无 409**，Revision #1 落库、状态→待重新评审）
  →Review findings 跳转用例→Optimizer 叙事→报告→材料/设置渲染；控制台 0 error，32 请求全 200。
- 真实渲染截图（无头浏览器 1440×900）：`screenshots/v2r-01-dashboard.png` 至 `v2r-11-settings.png` 共 11 张，
  覆盖工作台/运行列表/概览(Pipeline 全✓)/需求与AI分析/测试点/测试用例/AI评审/优化/报告/材料/设置。
  ★ 该 11 张已随仓库提交，并作为 V2 界面预览嵌入 `README.md`；核对过：截图渲染自 `static/v2/` 当前构建产物
  （构建 CSS 已含 P0 设计令牌，且工作树构建与 HEAD 逐字节一致），故截图即当前发布界面；
  逐张复查 + PNG 字节扫描确认无 API Key/base_url/凭据（设置页仅显示「已配置」徽标），业务内容仅为合成验收数据。
- 兼容性：Step 10.5 真实 Run `01M2QBWABYW3VTNYK5BBX2WDN4` 经 `GET /api/v2/runs/<id>` 仍可查询
  （status=done，counts=28 items / 174 points / 174 cases / 25 obligations）。
- 诚实呈现核查：材料引用「暂未接入」标注、Pipeline 不显示耗时、Optimizer 无 diff 说明、
  API Key 不回显——均已按后端真实能力呈现。

## 5. 已知限制与后续方向（不在本轮实施）

1. 同一次运行编辑用例后其他 Tab 依赖手动刷新/自动 reload（Drawer 保存后 bundle 自动刷新）。
2. Run 详情一次性并发拉 7 端点，超大用例集（数千）建议分页——当前 300 行截断 + 筛选兜底。
3. Project / TestTask（任务跨 Run、任务级归档/命名/材料）与 Material→Requirement Context→Runtime
   引用链路 = 未来独立后端设计（需路线评审）。
4. 导出 Excel/Markdown（V2 端点无此能力）、自动化执行（Step 12+）继续保持不伪装原则。
