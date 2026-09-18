// ============================================================
// 通用展示工具：时间/百分比/分数级别/状态中文标签
// 状态标签为「产品层显示名」，不改后端枚举本身（Run 状态机保持现状）。
// ============================================================

export function fmtTime(iso?: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? String(iso) : d.toLocaleString("zh-CN", { hour12: false });
}

export function timeAgo(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso).getTime();
  if (isNaN(d)) return "";
  const diff = Date.now() - d;
  const m = Math.floor(diff / 60000);
  if (m < 1) return "刚刚";
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  const day = Math.floor(h / 24);
  if (day < 30) return `${day} 天前`;
  return fmtTime(iso);
}

/** 0~1 比率 → 百分比文本（去掉多余的 .0） */
export function pct(v?: number | null): string | null {
  if (v == null) return null;
  const p = v * 100;
  return `${p % 1 === 0 ? p.toFixed(0) : p.toFixed(1)}%`;
}

export function scoreLevel(v?: number | null): "good" | "mid" | "low" | "na" {
  if (v == null) return "na";
  if (v >= 85) return "good";
  if (v >= 60) return "mid";
  return "low";
}

/** Run 状态 → 中文（底层枚举不变，仅展示层映射） */
export const RUN_STATUS_LABEL: Record<string, string> = {
  ingesting: "摄取需求中",
  parsing: "解析需求中",
  strategizing: "策略补充中",
  generating: "生成用例中",
  reviewing: "AI 评审中",
  optimizing: "去重优化中",
  done: "已完成",
  failed: "运行失败",
};

/** TestCase 状态 → 中文 */
export const CASE_STATUS_LABEL: Record<string, string> = {
  generated: "已生成",
  validated: "已校验",
  validation_failed: "校验失败",
  reviewed: "已评审",
  edited: "已编辑",
  re_review_required: "待重新评审",
  confirmed: "已确认",
  archived: "已归档",
};

/** Runtime 阶段（failed_step 取值域，与 core/v2/runtime.py STEP_ORDER 一致） */
export const STEP_LABEL: Record<string, string> = {
  ir: "需求解析",
  testpoints: "测试点生成",
  strategy: "策略补充",
  testcases: "测试用例生成",
  review: "AI 评审",
  optimizer: "去重优化",
};

export const DIMENSION_LABEL: Record<string, string> = {
  coverage: "覆盖率",
  accuracy: "准确性",
  executability: "可执行性",
  consistency: "一致性",
  missing_risk: "遗漏风险",
  duplication: "重复度",
};
