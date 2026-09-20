// ============================================================
// postbuild：为 templates/v2.html 的前端资源引用写入新版本号（缓存失效）。
// 纯构建阶段处理，不改任何后端 Python 代码 / Flask 路由。
// 用法：npm run build → vite build && node scripts/bump-template-version.mjs
// ============================================================

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const templatePath = join(here, "..", "..", "templates", "v2.html");

const version = Date.now().toString(36); // 短而唯一，如 "m9f3xk2a"
let html = readFileSync(templatePath, "utf8");
const before = html;
html = html.replace(/v2_react\.(js|css)\?v=[0-9a-z]+/g, (_m, ext) => `v2_react.${ext}?v=${version}`);

if (html === before) {
  console.error("[bump-template-version] templates/v2.html 未找到 v2_react.*?v= 引用，跳过版本号写入");
  process.exit(1);
}
writeFileSync(templatePath, html, "utf8");
console.log(`[bump-template-version] templates/v2.html 资源版本已更新为 v=${version}`);
