# 系统后台角色权限管理需求

## 1. 功能描述

后台系统通过角色（Role）控制用户可访问的功能资源与操作，支持角色分配与权限变更。

## 2. 数据字段（角色分配表单）

### 2.1 角色类型 role_type
- 数据类型：enum
- 必填
- 枚举值：`超级管理员` / `运营` / `审计`

### 2.2 账号状态 account_status
- 数据类型：enum
- 枚举值：`启用` / `停用`
- 停用账号不允许登录后台

## 3. 权限矩阵

资源：`user`（用户管理）、`content`（内容管理）、`audit_log`（审计日志）；操作：`view` / `create` / `edit` / `delete`。

| 角色 | 资源 | 操作 | 是否允许 |
|---|---|---|---|
| admin | user | view | ✅ 允许 |
| admin | user | create | ✅ 允许 |
| admin | user | edit | ✅ 允许 |
| admin | user | delete | ✅ 允许 |
| admin | content | delete | ✅ 允许 |
| admin | audit_log | view | ✅ 允许 |
| operator | user | view | ✅ 允许 |
| operator | user | create | ❌ 拒绝 |
| operator | user | edit | ✅ 允许 |
| operator | content | view | ✅ 允许 |
| operator | content | create | ✅ 允许 |
| operator | audit_log | view | ❌ 拒绝 |
| auditor | user | view | ❌ 拒绝 |
| auditor | content | delete | ❌ 拒绝 |
| auditor | audit_log | view | ✅ 允许 |

## 4. 业务规则

### 4.1 权限变更即时生效
- 规则名：`permission_take_effect`
- 期望：角色或权限变更后即时生效，用户下一次操作即按新权限约束

### 4.2 管理员敏感操作留痕
- 规则名：`admin_audit_trail`
- 期望：admin 对用户与内容的写操作（create/edit/delete）必须记录审计日志，操作后提示「操作已记录审计日志」

## 5. 提示文案

- 越权访问：「您没有该操作权限」
- 停用账号登录：「账号已停用，请联系管理员」
- 审计留痕：「操作已记录审计日志」

## 6. 验收标准

1. 权限矩阵中每个「拒绝」项都必须被拦截并提示「您没有该操作权限」
2. 权限矩阵中每个「允许」项都必须可执行成功
3. 停用账号在任何状态下都无法登录后台
