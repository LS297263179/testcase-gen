// ============================================================
// 路由与全局装配：HashRouter（无需后端 SPA fallback，Flask /v2 路由零改动）。
// ============================================================

import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./context/Auth";
import { ToastProvider } from "./components/Toast";
import { Dashboard } from "./pages/Dashboard";
import { Materials } from "./pages/Materials";
import { XmindPage } from "./pages/XmindPage";
import { Settings } from "./pages/Settings";
import { RunList } from "./run/RunList";
import { RunNew } from "./run/RunNew";
import { RunLayout } from "./run/RunLayout";
import { RunOverview } from "./run/RunOverview";
import { RequirementsTab } from "./run/RequirementsTab";
import { TestPointsTab } from "./run/TestPointsTab";
import { TestCasesTab } from "./run/TestCasesTab";
import { ReviewTab } from "./run/ReviewTab";
import { OptimizerTab } from "./run/OptimizerTab";
import { ReportTab } from "./run/ReportTab";

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <HashRouter>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/runs" element={<RunList />} />
            <Route path="/runs/new" element={<RunNew />} />
            <Route path="/runs/:runId" element={<RunLayout />}>
              <Route index element={<RunOverview />} />
              <Route path="requirements" element={<RequirementsTab />} />
              <Route path="test-points" element={<TestPointsTab />} />
              <Route path="test-cases" element={<TestCasesTab />} />
              <Route path="review" element={<ReviewTab />} />
              <Route path="optimizer" element={<OptimizerTab />} />
              <Route path="report" element={<ReportTab />} />
            </Route>
            <Route path="/resources/materials" element={<Materials />} />
            <Route path="/resources/xmind" element={<XmindPage />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </HashRouter>
      </ToastProvider>
    </AuthProvider>
  );
}
