// ============================================================
// V2 API 端点封装（/api/v2/*，共 14 个端点，与 web/v2_routes.py 一一对应）
// 原则：本轮 UI 只调用已存在的端点，不新增、不绕过 gating。
// ============================================================

import { apiJson, jsonInit } from "./client";
import type {
  CaseRevision,
  EditResult,
  OptimizerResult,
  PipelineRunResult,
  RequirementsContext,
  ReviewReport,
  Run,
  RunListItem,
  TestCase,
  TestPoint,
  TraceResult,
  CoverageDetail,
} from "../types";

// ---- 系统 ----

export interface Health {
  status: string;
  v2_ready: boolean;
  schema_version: number | null;
}

export const getHealth = () => apiJson<Health>("/api/v2/health");

// ---- Run 生命周期 ----

export const listRuns = () =>
  apiJson<{ success: boolean; items: RunListItem[] }>("/api/v2/runs").then((d) => d.items);

export const createRun = (title: string, text: string, sourceType = "text") =>
  apiJson<PipelineRunResult>("/api/v2/runs", jsonInit("POST", { title, text, source_type: sourceType }));

export const getRun = (runId: string) =>
  apiJson<{ success: boolean; run: Run }>(`/api/v2/runs/${runId}`).then((d) => d.run);

export const listTestPoints = (runId: string, provenance?: string) =>
  apiJson<{ test_points: TestPoint[] }>(
    `/api/v2/runs/${runId}/test-points${provenance ? `?provenance=${provenance}` : ""}`,
  ).then((d) => d.test_points);

export const listTestCases = (runId: string, status?: string) =>
  apiJson<{ test_cases: TestCase[] }>(
    `/api/v2/runs/${runId}/test-cases${status ? `?status=${status}` : ""}`,
  ).then((d) => d.test_cases);

export const getReview = (runId: string) =>
  apiJson<{ review: ReviewReport | null }>(`/api/v2/runs/${runId}/review`).then((d) => d.review);

export const getOptimizer = (runId: string) =>
  apiJson<{ optimizer: OptimizerResult }>(`/api/v2/runs/${runId}/optimizer`).then((d) => d.optimizer);

export const getCoverage = (runId: string) =>
  apiJson<{ coverage: CoverageDetail }>(`/api/v2/runs/${runId}/coverage`).then((d) => d.coverage);

export const getRequirements = (runId: string) =>
  apiJson<{ requirements: RequirementsContext }>(`/api/v2/runs/${runId}/requirements`).then((d) => d.requirements);

// ---- TestCase 人工操作（Step 9）+ 追溯（Step 6）----

export const editTestCase = (tcId: string, updates: Record<string, unknown>, expectedUpdatedAt: string) =>
  apiJson<{ success: boolean; result: EditResult }>(
    `/api/v2/test-cases/${tcId}/edit`,
    jsonInit("POST", { updates, expected_updated_at: expectedUpdatedAt }),
  ).then((d) => d.result);

export const reReview = (tcId: string, runId: string) =>
  apiJson<{ success: boolean; result: { run_id?: string } }>(
    `/api/v2/test-cases/${tcId}/re-review`,
    jsonInit("POST", { run_id: runId }),
  ).then((d) => d.result);

export const listRevisions = (tcId: string) =>
  apiJson<{ revisions: CaseRevision[] }>(`/api/v2/test-cases/${tcId}/revisions`).then((d) => d.revisions);

export const getTrace = (tcId: string) =>
  apiJson<{ trace: TraceResult }>(`/api/v2/test-cases/${tcId}/trace`).then((d) => d.trace);
