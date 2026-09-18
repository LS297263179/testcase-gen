// ============================================================
// V2 API 数据类型（与 web/v2_service.py 的序列化字段人工对齐）
// 说明：字段多为可选，UI 渲染时按实降级显示（后端能力边界内不伪造）。
// ============================================================

export interface RunCounts {
  items?: number;
  points?: number;
  cases?: number;
  obligations?: number;
}

export interface Run {
  id: string;
  status: string;
  doc_id?: string;
  requirement_version_id?: string;
  generation_config_id?: string;
  counts?: RunCounts;
  failed_step?: string | null;
  error_message?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface RunListItem {
  run_id: string;
  title: string;
  status: string;
  created_at: string;
  failed_step?: string | null;
}

export interface TestPoint {
  id: string;
  module: string;
  subcategory?: string;
  title: string;
  description?: string;
  dimension?: string;
  priority?: string;
  provenance?: string;
  technique?: string | null;
  obligation_id?: string | null;
  generation_scope?: string;
  strategy_params?: Record<string, unknown> | null;
  item_ids?: string[];
}

export interface TestStep {
  seq: number;
  action: string;
  data?: string | null;
  expected?: string | null;
}

export interface DataPlanItem {
  field: string;
  strategy: string;
  value?: string | number | boolean | null;
  source: string;
  generator: string;
  expected_valid?: boolean;
}

export interface TestCase {
  id: string;
  run_id?: string;
  display_id?: string;
  module?: string;
  title?: string;
  precondition?: string;
  steps?: TestStep[];
  expected?: string;
  priority?: string;
  type?: string;
  remark?: string;
  status?: string;
  provenance?: string;
  generation_mode?: string;
  confidence_level?: string;
  test_point_ids?: string[];
  validation_errors?: string[];
  data_plan?: DataPlanItem[];
  updated_at?: string;
}

export interface ReviewScores {
  coverage?: number;
  accuracy?: number;
  executability?: number;
  consistency?:
  number;
  missing_risk?: number;
  duplication?: number;
}

export interface ReviewFinding {
  id?: string;
  dimension: string;
  severity: string;
  target_type?: string;
  target_id?: string;
  issue: string;
  suggestion?: string | null;
  provenance?: string;
  auto_fixable?: boolean;
  detail?: Record<string, unknown> | null;
}

export interface CoverageDetail {
  strategy_obligation_coverage?: number | null;
  requirement_item_coverage?: number | null;
  uncovered_item_ids?: string[];
}

export interface ExecutabilityDetail {
  structural_score?: number;
  semantic_score?: number;
  structural_weight?: number;
  semantic_weight?: number;
}

export interface ReviewReport {
  id?: string;
  run_id?: string;
  revision?: number;
  trigger_type?: string;
  scores?: ReviewScores;
  overall_score?: number;
  summary?: string;
  findings?: ReviewFinding[];
  coverage_detail?: CoverageDetail | null;
  executability_detail?: ExecutabilityDetail | null;
  dimension_reasons?: Record<string, string>;
}

export interface OptimizerResult {
  run_id?: string;
  archived_count?: number;
  archived_cases?: { id: string; display_id?: string; title?: string; status?: string }[];
  note?: string;
}

export interface FieldSpec {
  name: string;
  label?: string;
  data_type?: string;
  required?: boolean;
  nullable?: boolean;
  min_length?: number | null;
  max_length?: number | null;
  min_value?: number | null;
  max_value?: number | null;
  pattern?: string | null;
  enum_values?: string[];
  unique?: boolean;
  example?: string | null;
}

export interface BusinessRule {
  name: string;
  expression_type?: string;
  expression: string;
  expected?: string | null;
}

export interface PermissionRule {
  role: string;
  resource: string;
  action: string;
  allowed: boolean;
  condition?: string | null;
}

export interface RequirementItem {
  id: string;
  seq?: number;
  type?: string;
  module: string;
  statement: string;
  fields?: FieldSpec[];
  rules?: BusinessRule[];
  permissions?: PermissionRule[];
  acceptance_criteria?: string[];
  source_ref?: { locator?: string; value?: string } | null;
  confidence?: number;
  confidence_level?: string;
  provenance?: string;
  priority_hint?: string | null;
}

export interface RequirementsContext {
  run_id: string;
  status?: string;
  doc?: { id?: string; title?: string; source_type?: string; latest_version_id?: string | null } | null;
  version?: { id?: string; version_no?: number; provenance?: string; change_summary?: string | null } | null;
  items: RequirementItem[];
  item_count: number;
  field_count: number;
  rule_count: number;
  permission_count: number;
  modules: string[];
}

export interface CaseRevision {
  id?: string;
  revision_no?: number;
  provenance?: string;
  changed_fields?: string[];
  changed_by?: string | null;
  change_source?: string | null;
  created_at?: string;
  snapshot?: Record<string, unknown>;
}

export interface TraceResult {
  test_case?: { id?: string; display_id?: string; title?: string } | null;
  test_points?: { id?: string; title?: string; provenance?: string; module?: string }[];
  requirement_items?: { id?: string; module?: string; statement?: string; type?: string }[];
  doc?: { title?: string } | null;
  version?: { version_no?: number } | null;
  source_refs?: unknown[];
  issues?: string[];
}

export interface EditResult {
  success?: boolean;
  new_status?: string;
  issues?: string[];
  validation_errors?: string[];
}

export interface PipelineRunResult {
  success?: boolean;
  run_id?: string | null;
  status?: string;
  failed_step?: string | null;
  error_message?: string | null;
  test_point_count?: number;
  test_case_count?: number;
}

// V1 复用端点（项目材料）
export interface MaterialListItem {
  id: number;
  title: string;
  content?: string;
  content_preview?: string;
  created_at?: string;
  image_count?: number;
}

export interface MaterialDetail extends MaterialListItem {
  images?: { id: number; filename?: string; caption?: string }[];
}
