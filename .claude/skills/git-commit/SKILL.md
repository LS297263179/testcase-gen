---
name: git-commit
description: "安全提交代码到 GitHub + Gitee 双远程 — 提交前审查、自动推送、失败告警。适合开发完成后需要提交和推送代码时使用"
argument-hint: "<commit-message>"
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
user-invocable: true
---

# /git-commit

安全提交代码到 GitHub + Gitee 双远程，提交前自动审查，推送失败即时告警。

## 用法
```
/git-commit <提交信息>        → 审查 → 提交 → 推送双远程
/git-commit --no-push <信息>  → 只提交不推送
/git-commit --amend           → 修改上一次提交
/git-commit --force           → 审查后强制推送（仅限紧急修复）
```

## 流程
```
代码修改 → 安全审查 → git add → git commit → 推送到 GitHub → 推送到 Gitee → 报告结果
```

---

## 提交前安全审查

### 1. 敏感信息检查
逐文件检查暂存区 diff，拦截以下内容：
- **API 密钥**：`api_key`、`api-key`、`apikey`、`sk-`（OpenAI key 前缀）、`AKIA`（AWS key 前缀）
- **密码/令牌**：`password`、`secret`、`token`、`credential` 等字样的赋值
- **配置泄露**：`config.yaml` 被意外 staged（该文件不追踪 git）
- **环境文件**：`.env`、`.env.local`、`*.key`、`*cert.pem` 等
- **内网地址**：`10.x.x.x`、`192.168.x.x`、`127.0.0.1` 等 IP 硬编码

如果发现敏感信息：**拦截提交**，列出文件路径和行号，提示用户移除。

### 2. 规范化检查
- [ ] 已同步最新代码（`git pull --rebase`）
- [ ] ruff lint 通过（`ruff check .`）
- [ ] ruff format 通过（`ruff format --check .`）
- [ ] pytest 通过（至少核心测试）
- [ ] 没有在 main 分支直接开发
- [ ] 没有空提交（`git diff --cached` 有内容）
- [ ] 没有 `print()`/`console.log()` 调试残留（非必要）
- [ ] 没有大文件（单文件 > 1MB，提醒用户确认）
- [ ] 没有二进制文件被意外 staged（`.exe`、`.dll`、`.so`、`.dmg`、`.pkl` 等）

### 3. 提交信息规范
- **格式**：`<type>: <中文描述>`
- **type**：`feat` / `fix` / `refactor` / `docs` / `test` / `chore` / `style`
- **示例**：`feat: 新增测试点导出功能`
- 长度不超过 72 字符
- 如果描述过长，用 `-m` 多行提交

---

## 推送策略

### 双远程推送
```bash
# GitHub（origin）
git push origin <branch>

# Gitee（绕过代理）
git -c http.proxy="" -c https.proxy="" push gitee <branch>
```

### 失败处理
| 情况 | 行为 |
|------|------|
| GitHub 成功，Gitee 失败 | ✅ 提交成功，❌ 显示 Gitee 失败原因，提醒手动处理 |
| GitHub 失败，Gitee 成功 | ❌ 显示 GitHub 失败原因，提醒手动处理 |
| 两者都失败 | ❌ 列出双方失败原因，建议检查网络/权限后重试 |
| 两者都成功 | ✅ 提交成功，显示 commit hash 和两个 remote 推送确认 |

### 常见失败原因排查
- **Gitee 推送失败**：最常见是代理问题 → `git -c http.proxy="" -c https.proxy="" push gitee <branch>`
- **GitHub 推送失败**：检查 SSH key / token 是否有效、网络是否通畅
- **rejected**：远程有更新 → 先 `git pull --rebase` 再重试
- **403/401**：认证失效 → 检查 remote URL 和凭据

---

## 输出格式
```
🔍 提交前审查...

  [安全] ✅ 未检测到敏感信息
  [规范] ✅ ruff check 通过 | ruff format 通过 | pytest 通过
  [信息] 📝 feat: 新增测试点导出功能

📦 提交中...
  ✅ commit abc1234 创建成功

🚀 推送中...
  ✅ GitHub (origin)     → 推送成功 (main -> main)
  ❌ Gitee (gitee)       → 推送失败
     原因: fatal: unable to access 'https://gitee.com/...': Failed to connect
     建议: 运行 git -c http.proxy="" -c https.proxy="" push gitee main

─────────────────
  ⚠️  提交已成功，但 Gitee 推送失败，请手动处理
```

## 约束
- 除非用户明确要求，不直接推 main 分支
- `--force` 选项仅在紧急修复时使用，且会额外确认
- 不修改代码逻辑，只执行 git 操作和安全审查
