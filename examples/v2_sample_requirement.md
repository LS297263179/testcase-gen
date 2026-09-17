# 订单退款申请功能需求（V2 示例）

> 本文档为 V2 全链路真实运行（Step 10.5）示例需求。设计目标：让 Step 2 IR Parser 能抽取出
> **FieldSpec / BusinessRule / PermissionRule** 三类结构化数据，使 Step 4 策略引擎能真派生
> BOUNDARY_VALUE / EQUIVALENCE_CLASS / PERMISSION_MATRIX 三种 obligation。

## 1. 功能描述

用户在订单详情页发起退款申请，填写退款金额、退款原因、问题描述、联系方式后提交；
系统根据订单状态、金额大小、用户角色执行不同的审批流程。

## 2. 数据字段（申请退款表单）

### 2.1 退款金额 refund_amount
- 数据类型：float（保留 2 位小数）
- 必填，不可为空
- 最小值 0.01，最大值不超过订单实付金额
- 单位：人民币元（CNY）
- 示例：`99.90`

### 2.2 退款原因 reason
- 数据类型：enum
- 必填
- 枚举值：`质量问题` / `描述不符` / `物流损坏` / `七天无理由` / `其他`
- 默认值：无（用户必须主动选择）

### 2.3 问题描述 description
- 数据类型：string
- 非必填（可空）
- 最小长度 0，最大长度 500 字符
- 支持中英文与常见标点

### 2.4 联系方式 contact_phone
- 数据类型：phone（11 位中国大陆手机号）
- 必填
- 正则：`^1[3-9]\d{9}$`
- 唯一性：无（多个订单可复用同一手机号）

### 2.5 退款凭证图片 evidence_images
- 数据类型：string（URL 列表）
- 非必填（可空）
- 最大数量 5 张
- 单张图片最大 5MB

## 3. 业务规则

### 3.1 申请时效
- 规则名：`refund_time_window`
- 表达式类型：comparison
- 表达式：`current_date - order.completed_at <= 7 days`
- 输入：`current_date`, `order.completed_at`
- 期望：订单完成后 7 天内可申请退款，超过 7 天系统拒绝

### 3.2 重复申请禁止
- 规则名：`no_duplicate_refund`
- 表达式类型：enum
- 表达式：`order.refund_status in {NOT_APPLIED, REJECTED}`
- 输入：`order.refund_status`
- 期望：只有状态为「未申请」或「已驳回」的订单可申请退款；「审核中」「已退款」的订单不可重复申请

### 3.3 大额审核
- 规则名：`large_amount_review`
- 表达式类型：comparison
- 表达式：`refund_amount > 1000`
- 输入：`refund_amount`
- 期望：退款金额大于 1000 元时必须经管理员审核通过才能进入财务打款流程

### 3.4 金额上限
- 规则名：`amount_upper_bound`
- 表达式类型：comparison
- 表达式：`refund_amount <= order.paid_amount`
- 输入：`refund_amount`, `order.paid_amount`
- 期望：退款金额不得超过订单实付金额

### 3.5 凭证要求
- 规则名：`evidence_required_for_quality_issue`
- 表达式类型：natural_language
- 表达式：当退款原因为「质量问题」或「物流损坏」时，必须上传至少 1 张凭证图片
- 输入：`reason`, `evidence_images`
- 期望：缺少凭证时提交按钮置灰，前端提示「请上传凭证图片」

## 4. 权限矩阵

| 角色 | 资源 | 操作 | 是否允许 | 前置条件 |
|---|---|---|---|---|
| customer | refund_application | create | ✅ 允许 | 仅能对本人订单发起 |
| customer | refund_application | view | ✅ 允许 | 仅能查看本人申请 |
| customer | refund_application | cancel | ✅ 允许 | 仅在状态为「审核中」时可撤销 |
| customer | refund_application | approve | ❌ 拒绝 | — |
| customer_service | refund_application | view | ✅ 允许 | 可查看所有申请 |
| customer_service | refund_application | create | ✅ 允许 | 可代客户发起（需记录代操作日志） |
| customer_service | refund_application | approve | ❌ 拒绝 | — |
| admin | refund_application | view | ✅ 允许 | — |
| admin | refund_application | approve | ✅ 允许 | 金额 > 1000 时必须由 admin 审批 |
| admin | refund_application | reject | ✅ 允许 | 需填写驳回原因 |
| finance | refund_application | view | ✅ 允许 | 仅可见已审批通过的申请 |
| finance | refund_payout | execute | ✅ 允许 | 前置：申请状态 = APPROVED |
| finance | refund_application | approve | ❌ 拒绝 | — |

## 5. 状态机

退款申请状态流转：
```
NOT_APPLIED → PENDING（用户提交）
PENDING → APPROVED（管理员通过）→ PAID（财务打款）→ CLOSED
PENDING → REJECTED（管理员驳回）→ NOT_APPLIED（用户可重新申请）
PENDING → CANCELLED（用户撤销）
```

## 6. 异常场景

- 订单不存在或不属于当前用户：提示「订单不存在或无权访问」
- 订单未完成：提示「订单未完成，暂不可申请退款」
- 超过 7 天时效：提示「已超过退款申请时效」
- 网络异常导致提交失败：提示「网络异常，请稍后重试」，前端保留用户已填内容
- 上传凭证图片超过 5MB：提示「单张图片不得超过 5MB」

## 7. 验收标准

1. 用户提交退款申请后，系统在 3 秒内返回提交结果
2. 大额（>1000）退款必须经管理员审核，未审核前财务无法看到打款入口
3. 已退款订单再次访问申请页面时，页面显示「该订单已退款」并禁用所有表单字段
4. 权限矩阵中所有「拒绝」项，接口层必须返回 403 而非 200
5. 退款金额边界值（0.01 / 订单金额 / 订单金额+0.01）必须逐一验证
6. 手机号格式非法（10 位 / 12 位 / 非 1[3-9] 开头）必须被前端与后端同时拦截
