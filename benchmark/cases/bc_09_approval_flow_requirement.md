# 差旅报销审批流程需求

## 1. 功能描述

员工提交差旅报销单，经部门主管审批后流转出纳打款；大额报销需财务总监二次审批。

## 2. 数据字段（报销单）

### 2.1 报销金额 expense_amount
- 数据类型：float（保留 2 位小数）
- 必填，最小值 0.01，最大值 50000

### 2.2 报销事由 expense_reason
- 数据类型：string
- 必填，最小长度 10，最大长度 200 字符

### 2.3 驳回原因 reject_reason
- 数据类型：string
- 审批驳回时必填，最大长度 200 字符

## 3. 业务规则

### 3.1 大额二级审批
- 规则名：`large_expense_second_review`
- 表达式：`expense_amount > 5000`
- 期望：金额大于 5000 元的报销单在部门主管审批通过后，必须经财务总监二次审批，否则提示「需财务总监审批」

### 3.2 禁止自审批
- 规则名：`no_self_approve`
- 期望：任何审批人不能审批本人提交的报销单，违例提示「不能审批本人提交的报销单」

### 3.3 驳回必填原因
- 规则名：`reject_reason_required`
- 期望：审批驳回时必须填写驳回原因，否则提示「请填写驳回原因」

### 3.4 审批通过流转
- 规则名：`approve_to_cashier`
- 期望：审批全部通过后报销单进入出纳打款队列，提示「审批通过，流转至出纳」

## 4. 权限矩阵

| 角色 | 资源 | 操作 | 是否允许 | 前置条件 |
|---|---|---|---|---|
| employee | expense_claim | create | ✅ 允许 | — |
| employee | expense_claim | approve | ❌ 拒绝 | 提示「无审批权限」 |
| dept_manager | expense_claim | approve | ✅ 允许 | 金额 ≤ 5000 终审；金额 > 5000 一级审批 |
| finance_director | expense_claim | approve | ✅ 允许 | 金额 > 5000 的二级终审 |
| cashier | expense_claim | pay | ✅ 允许 | 仅在审批全部通过后 |
| cashier | expense_claim | approve | ❌ 拒绝 | — |

## 5. 提示文案

- 提交成功：「报销单已提交，等待部门主管审批」
- 审批人越权：「您没有该报销单的审批权限」
- 出纳未审批打款：「该报销单尚未完成审批」
