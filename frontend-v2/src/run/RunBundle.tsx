// ============================================================
// RunBundle：单个测试运行的全量数据总线。
// Run 详情页挂载时并发拉取 7 个只读端点（Promise.allSettled，单个失败不拖垮整页），
// 各 Tab 复用同一批数据；编辑/重评审后调用 reload() 刷新。
// 诚实约束：review/optimizer/requirements 可能为 null（后端无数据时不伪造）。
// ============================================================

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { getCoverage, getOptimizer, getRequirements, getReview, getRun, listTestCases, listTestPoints } from "../api/v2";
import type { CoverageDetail, OptimizerResult, RequirementsContext, ReviewReport, Run, TestCase, TestPoint } from "../types";
import { LoadingBlock, ErrorAlert } from "../components/ui";

export interface RunBundle {
  run: Run;
  title: string;
  testPoints: TestPoint[];
  testCases: TestCase[];
  review: ReviewReport | null;
  coverage: CoverageDetail | null;
  optimizer: OptimizerResult | null;
  requirements: RequirementsContext | null;
  reload: () => void;
  /** 当前打开的 TestCase 详情 Drawer（跨 Tab 共享；action 支持直接进编辑/重评审） */
  caseDrawerId: string | null;
  caseDrawerAction: "" | "edit";
  openCase: (id: string | null, action?: "" | "edit") => void;
}

const Ctx = createContext<RunBundle | null>(null);

export function useRunBundle(): RunBundle {
  const b = useContext(Ctx);
  if (!b) throw new Error("useRunBundle 必须在 RunLayout 内使用");
  return b;
}

export function RunBundleProvider({ runId, children }: { runId: string; children: ReactNode }) {
  const [bundle, setBundle] = useState<RunBundle | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);
  const [caseDrawer, setCaseDrawer] = useState<{ id: string | null; action: "" | "edit" }>({ id: null, action: "" });

  useEffect(() => {
    let cancelled = false;
    setError(null);
    (async () => {
      try {
        const run = await getRun(runId); // run 不存在 → 404 抛错，整页报错
        const [tp, tc, rv, cov, opt, req] = await Promise.allSettled([
          listTestPoints(runId),
          listTestCases(runId),
          getReview(runId),
          getCoverage(runId),
          getOptimizer(runId),
          getRequirements(runId),
        ]);
        if (cancelled) return;
        const ok = <T,>(s: PromiseSettledResult<T>) => (s.status === "fulfilled" ? s.value : null);
        setBundle({
          run,
          title: ok(req)?.doc?.title || "(无标题)",
          testPoints: ok(tp) || [],
          testCases: ok(tc) || [],
          review: ok(rv),
          coverage: ok(cov),
          optimizer: ok(opt),
          requirements: ok(req),
          reload: () => setTick((t) => t + 1),
          caseDrawerId: caseDrawer.id,
          caseDrawerAction: caseDrawer.action,
          openCase: (id, action = "") => setCaseDrawer({ id, action }),
        });
      } catch (e) {
        if (!cancelled) setError(e);
      }
    })();
    return () => {
      cancelled = true;
    };
    // caseDrawerId 不进依赖：Drawer 开关不触发重新拉取
  }, [runId, tick]); // eslint-disable-line react-hooks/exhaustive-deps

  // Drawer 开关不触发重新拉取，仅同步到已有 bundle
  useEffect(() => {
    setBundle((b) => (b ? { ...b, caseDrawerId: caseDrawer.id, caseDrawerAction: caseDrawer.action } : b));
  }, [caseDrawer]);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  if (error) return <ErrorAlert error={error} onRetry={() => setTick((t) => t + 1)} />;
  if (!bundle) return <LoadingBlock text="正在加载测试运行数据..." />;
  return <Ctx.Provider value={{ ...bundle, reload }}>{children}</Ctx.Provider>;
}
