// ============================================================
// AI 测试工程平台 · V2 前端逻辑（Step 10.4）
//
// ★ 决策2：V2 不重造认证。认证仍走 V1 session；未登录由服务端 /v2 路由 302 跳回 "/"，
//          前端 checkAuth() 再兜底跳 "/"（双保险）。所有受保护请求经 authFetch()，401 → 跳 "/"。
// ★ P0-4 一致：POST /api/v2/runs 为【同步等待】，本页不做 SSE / 异步轮询 / 后台 Job；
//              生成期间仅显示 loading 文案，等待 HTTP 响应返回后再渲染。
// ★ 阶段 A（自动）：[生成] 触发 Runtime 执行 Step 2~8。
//   阶段 B（主动）：[编辑] / [重新评审] 由用户点击触发（Step 9），不在自动链内。
// 复用 V1 基础样式（style.css 只读），V2 组件样式见 v2_style.css。
// ============================================================

let csrfToken = "";
let isGenerating = false;
let currentRunId = null;
let currentRun = null;
let currentReview = null;
let currentCases = [];
let currentCase = null;
let editStepsOriginal = "";

// ------------------------------------------------------------
// 基础工具：转义 / 时间 / toast / 请求头 / 受保护 fetch
// ------------------------------------------------------------
function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtTime(iso) {
    if (!iso) return "-";
    const d = new Date(iso);
    return isNaN(d) ? String(iso) : d.toLocaleString("zh-CN", { hour12: false });
}

function showToast(msg, type = "success") {
    const c = document.getElementById("toastContainer");
    const t = document.createElement("div");
    t.className = "toast " + type;
    const icon = type === "error" ? "!" : type === "warning" ? "!" : "OK";
    t.innerHTML = '<span class="toast-icon">' + icon + '</span><span class="toast-msg">' + esc(msg) + "</span>";
    c.appendChild(t);
    requestAnimationFrame(() => t.classList.add("show"));
    setTimeout(() => { t.classList.add("hiding"); setTimeout(() => t.remove(), 400); }, 3400);
}

function _headers(h = {}) { if (csrfToken) h["X-CSRF-Token"] = csrfToken; return h; }

async function authFetch(u, o = {}) {
    o.headers = _headers(o.headers || {});
    const r = await fetch(u, o);
    if (r.status === 401) { window.location = "/"; throw new Error("登录已过期"); }
    return r;
}

function showError(id, html) { const e = document.getElementById(id); e.innerHTML = html; e.style.display = "block"; }
function hideError(id) { const e = document.getElementById(id); e.style.display = "none"; e.innerHTML = ""; }

// ------------------------------------------------------------
// 认证（决策2：不重造登录）
// ------------------------------------------------------------
async function checkAuth() {
    try {
        const r = await fetch("/api/me");
        const d = await r.json();
        if (!d.logged_in) { window.location = "/"; return; }
        if (d.csrf_token) csrfToken = d.csrf_token;
        document.getElementById("v2Username").textContent = d.user.username;
        loadRuns();
    } catch (e) {
        window.location = "/";  // 兜底：路由已服务端拦截，此处双保险
    }
}

async function doLogout() {
    try { await fetch("/api/logout", { method: "POST" }); } catch (e) { /* 忽略 */ }
    window.location = "/";
}

// ------------------------------------------------------------
// 渲染 helper：provenance 标签 / 状态色标 / 分数条
// ------------------------------------------------------------
function provBadge(prov) {
    const p = (prov || "").toLowerCase();
    const cls = p === "llm" ? "prov-llm" : p === "strategy" ? "prov-strategy" : "prov-other";
    return '<span class="v2-prov ' + cls + '">' + esc((prov || "-").toUpperCase()) + "</span>";
}

function caseStatusChip(status) {
    const s = (status || "").toLowerCase();
    return '<span class="v2-status st-' + esc(s) + '">' + esc(s || "-") + "</span>";
}

function runStatusChip(status) {
    const s = (status || "").toLowerCase();
    return '<span class="v2-runstatus rs-' + esc(s) + '">' + esc(s || "-") + "</span>";
}

function scoreLevel(v) { if (v == null) return "na"; if (v >= 85) return "good"; if (v >= 60) return "mid"; return "low"; }

function kv(k, vHtml) { return '<div class="v2-kv"><span class="v2-k">' + esc(k) + '</span><span class="v2-v">' + vHtml + "</span></div>"; }

function bar(label, valText, widthPct, levelCls) {
    const w = Math.max(0, Math.min(100, widthPct || 0));
    return '<div class="v2-score-item"><div class="v2-score-label">' + esc(label) + " <b>" + esc(valText) + "</b></div>" +
        '<div class="v2-bar"><div class="v2-bar-fill ' + (levelCls || "") + '" style="width:' + w + '%"></div></div></div>';
}

// ------------------------------------------------------------
// Run 列表
// ------------------------------------------------------------
async function loadRuns() {
    try {
        const r = await authFetch("/api/v2/runs");
        const d = await r.json();
        const items = d.items || [];
        const tbody = document.getElementById("runsTableBody");
        const empty = document.getElementById("runsEmpty");
        const table = document.getElementById("runsTable");
        if (!items.length) { table.style.display = "none"; empty.style.display = "block"; tbody.innerHTML = ""; return; }
        table.style.display = ""; empty.style.display = "none";
        tbody.innerHTML = items.map((it) =>
            '<tr onclick="openRun(\'' + it.run_id + '\')">' +
            "<td>" + esc(it.title) + "</td>" +
            "<td>" + runStatusChip(it.status) + "</td>" +
            '<td class="v2-muted">' + esc(fmtTime(it.created_at)) + "</td>" +
            "<td>" + (it.failed_step ? '<span class="v2-failed-step">' + esc(it.failed_step) + "</span>" : '<span class="v2-muted">-</span>') + "</td>" +
            '<td><button class="btn btn-sm btn-ghost" onclick="event.stopPropagation();openRun(\'' + it.run_id + '\')">查看</button></td>' +
            "</tr>").join("");
    } catch (e) {
        showToast("加载 Run 列表失败: " + e.message, "error");
    }
}

// ------------------------------------------------------------
// Run 详情（并发拉 6 个查询端点）
// ------------------------------------------------------------
function setRunLoading() {
    ["runSummary", "runCoverage", "runTestPoints", "runTestCases", "runReview", "runOptimizer"].forEach((id) => {
        document.getElementById(id).innerHTML = '<span class="v2-muted">加载中...</span>';
    });
}

async function openRun(runId) {
    currentRunId = runId;
    document.getElementById("runDetail").style.display = "";
    document.getElementById("runDetailTitle").textContent = "(" + runId + ")";
    setRunLoading();
    try {
        const [runR, tpR, tcR, rvR, covR, optR] = await Promise.all([
            authFetch("/api/v2/runs/" + runId),
            authFetch("/api/v2/runs/" + runId + "/test-points"),
            authFetch("/api/v2/runs/" + runId + "/test-cases"),
            authFetch("/api/v2/runs/" + runId + "/review"),
            authFetch("/api/v2/runs/" + runId + "/coverage"),
            authFetch("/api/v2/runs/" + runId + "/optimizer"),
        ]);
        const runD = await runR.json(), tpD = await tpR.json(), tcD = await tcR.json();
        const rvD = await rvR.json(), covD = await covR.json(), optD = await optR.json();
        currentRun = runD.run;
        currentReview = rvD.review;
        currentCases = tcD.test_cases || [];
        renderRunSummary(currentRun);
        renderCoverage(covD.coverage);
        renderTestPoints(tpD.test_points || []);
        renderTestCases(currentCases);
        renderReview(currentReview);
        renderOptimizer(optD.optimizer);
        document.getElementById("runDetail").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
        showToast("加载 Run 详情失败: " + e.message, "error");
    }
}

function renderRunSummary(run) {
    const el = document.getElementById("runSummary");
    if (!run) { el.innerHTML = '<span class="v2-muted">无数据</span>'; return; }
    const c = run.counts || {};
    let html = '<div class="v2-kv-grid">';
    html += kv("Run 状态", runStatusChip(run.status));
    html += kv("需求项 items", esc(c.items ?? 0));
    html += kv("测试点 points", esc(c.points ?? 0));
    html += kv("用例 cases", esc(c.cases ?? 0));
    html += kv("覆盖义务 obligations", esc(c.obligations ?? 0));
    html += kv("创建时间", esc(fmtTime(run.created_at)));
    html += "</div>";
    if ((run.status || "").toLowerCase() === "failed") {
        html += '<div class="v2-alert v2-alert-error"><b>失败步骤：</b>' + esc(run.failed_step || "-") +
            "<br><b>错误信息：</b>" + esc(run.error_message || "-") + "</div>";
    }
    el.innerHTML = html;
}

function renderCoverage(cov) {
    const el = document.getElementById("runCoverage");
    if (!cov) { el.innerHTML = '<span class="v2-muted">无覆盖数据</span>'; return; }
    const so = cov.strategy_obligation_coverage, ri = cov.requirement_item_coverage;
    const pct = (v) => (v == null ? null : Math.round(v * 1000) / 10);
    let html = '<div class="v2-score-grid">';
    html += bar("Strategy Obligation", so == null ? "N/A" : pct(so) + "%", so == null ? 0 : so * 100, "cov");
    html += bar("Requirement Item", ri == null ? "N/A" : pct(ri) + "%", ri == null ? 0 : ri * 100, "cov");
    html += "</div>";
    const unc = cov.uncovered_item_ids || [];
    html += '<div class="v2-muted v2-small">未覆盖需求项：' +
        (unc.length ? unc.length + " 个（" + esc(unc.slice(0, 5).join(", ")) + (unc.length > 5 ? " ..." : "") + "）" : "无") + "</div>";
    el.innerHTML = html;
}

function renderTestPoints(points) {
    const el = document.getElementById("runTestPoints");
    if (!points.length) { el.innerHTML = '<span class="v2-muted">无测试点</span>'; return; }
    el.innerHTML = '<div class="v2-muted v2-small">共 ' + points.length + ' 个</div>' +
        '<table class="v2-table"><thead><tr><th>模块</th><th>子分类</th><th>标题</th><th>维度</th><th>优先级</th><th>来源</th></tr></thead><tbody>' +
        points.map((p) =>
            "<tr><td>" + esc(p.module) + "</td><td>" + esc(p.subcategory) + "</td>" +
            '<td title="' + esc(p.description || "") + '">' + esc(p.title) + "</td>" +
            "<td>" + esc(p.dimension || "-") + "</td><td>" + esc(p.priority || "-") + "</td>" +
            "<td>" + provBadge(p.provenance) + "</td></tr>").join("") +
        "</tbody></table>";
}

function renderTestCases(cases) {
    const el = document.getElementById("runTestCases");
    if (!cases.length) { el.innerHTML = '<span class="v2-muted">无测试用例</span>'; return; }
    el.innerHTML = '<div class="v2-muted v2-small">共 ' + cases.length + ' 条（点击行进详情）</div>' +
        '<table class="v2-table"><thead><tr><th>编号</th><th>模块</th><th>标题</th><th>优先级</th><th>类型</th><th>状态</th><th>来源</th></tr></thead><tbody>' +
        cases.map((c) =>
            '<tr onclick="openTestCase(\'' + c.id + '\')">' +
            "<td>" + esc(c.display_id) + "</td><td>" + esc(c.module) + "</td><td>" + esc(c.title) + "</td>" +
            "<td>" + esc(c.priority) + "</td><td>" + esc(c.type) + "</td>" +
            "<td>" + caseStatusChip(c.status) + "</td><td>" + provBadge(c.provenance) + "</td></tr>").join("") +
        "</tbody></table>";
}

function renderReview(review) {
    const el = document.getElementById("runReview");
    if (!review) { el.innerHTML = '<span class="v2-muted">无评审报告</span>'; return; }
    const sc = review.scores || {};
    const dims = [["coverage", "覆盖率"], ["accuracy", "准确性"], ["executability", "可执行性"],
        ["consistency", "一致性"], ["missing_risk", "遗漏风险"], ["duplication", "重复度"]];
    let html = '<div class="v2-review-head">Overall：<b class="v2-overall">' + esc(review.overall_score ?? "-") +
        "</b> · 第 " + esc(review.revision ?? "-") + " 轮 · 触发 " + esc(review.trigger_type || "-") + "</div>";
    if (review.summary) html += '<div class="v2-muted v2-small">' + esc(review.summary) + "</div>";
    html += '<div class="v2-score-grid">';
    for (const [k, label] of dims) {
        const v = sc[k];
        html += bar(label, v == null ? "-" : v, v == null ? 0 : v, "score-" + scoreLevel(v));
    }
    html += "</div>";
    const ex = review.executability_detail;
    if (ex) {
        html += '<div class="v2-muted v2-small">可执行性明细：结构 ' + esc(ex.structural_score) + " x " + esc(ex.structural_weight) +
            " + 语义 " + esc(ex.semantic_score) + " x " + esc(ex.semantic_weight) + "</div>";
    }
    const f = review.findings || [];
    if (f.length) {
        html += '<div class="v2-subtitle-sm">Findings（' + f.length + "）</div>" + f.map(findingHtml).join("");
    } else {
        html += '<div class="v2-muted v2-small">无 finding。</div>';
    }
    el.innerHTML = html;
}

function findingHtml(x) {
    return '<div class="v2-finding sev-' + esc((x.severity || "").toLowerCase()) + '">' +
        '<span class="v2-finding-dim">' + esc(x.dimension) + "</span>" +
        '<span class="v2-sev">' + esc(x.severity) + "</span>" + provBadge(x.provenance) +
        (x.auto_fixable ? '<span class="v2-autofix">auto_fixable</span>' : "") +
        '<div class="v2-finding-issue">' + esc(x.issue) + "</div>" +
        (x.suggestion ? '<div class="v2-finding-sug">建议：' + esc(x.suggestion) + "</div>" : "") +
        "</div>";
}

function renderOptimizer(opt) {
    const el = document.getElementById("runOptimizer");
    if (!opt) { el.innerHTML = '<span class="v2-muted">无优化数据</span>'; return; }
    let html = '<div class="v2-kv-grid">' + kv("归档用例数", esc(opt.archived_count ?? 0)) + "</div>";
    if (opt.note) html += '<div class="v2-muted v2-small">' + esc(opt.note) + "</div>";
    const ac = opt.archived_cases || [];
    if (ac.length) {
        html += '<table class="v2-table"><thead><tr><th>编号</th><th>标题</th><th>状态</th></tr></thead><tbody>' +
            ac.map((c) => "<tr><td>" + esc(c.display_id) + "</td><td>" + esc(c.title) + "</td><td>" + caseStatusChip(c.status) + "</td></tr>").join("") +
            "</tbody></table>";
    }
    el.innerHTML = html;
}

// ------------------------------------------------------------
// TestCase 详情（阶段 B 入口）
// ------------------------------------------------------------
async function openTestCase(tcId) {
    const c = currentCases.find((x) => x.id === tcId);
    currentCase = c || { id: tcId };
    document.getElementById("caseDetail").style.display = "";
    document.getElementById("caseDetailTitle").textContent = "(" + tcId + ")";
    renderCaseBasic(currentCase);
    renderCaseFindings(currentCase);
    document.getElementById("caseRevisions").innerHTML = '<span class="v2-muted">加载中...</span>';
    document.getElementById("caseTrace").innerHTML = '<span class="v2-muted">加载中...</span>';
    try {
        const [revR, trR] = await Promise.all([
            authFetch("/api/v2/test-cases/" + tcId + "/revisions"),
            authFetch("/api/v2/test-cases/" + tcId + "/trace"),
        ]);
        const revD = await revR.json(), trD = await trR.json();
        renderRevisions(revD.revisions || []);
        renderTrace(trD.trace);
    } catch (e) {
        showToast("加载用例详情失败: " + e.message, "error");
    }
    document.getElementById("caseDetail").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderCaseBasic(c) {
    const el = document.getElementById("caseBasic");
    if (!c || !c.title) { el.innerHTML = '<span class="v2-muted">无数据（请从 Run 详情的用例列表进入）</span>'; return; }
    const steps = (c.steps || []).slice().sort((a, b) => a.seq - b.seq);
    let html = '<div class="v2-kv-grid">';
    html += kv("编号", esc(c.display_id || "-"));
    html += kv("模块", esc(c.module || "-"));
    html += kv("优先级", esc(c.priority || "-"));
    html += kv("类型", esc(c.type || "-"));
    html += kv("状态", caseStatusChip(c.status));
    html += kv("来源", provBadge(c.provenance));
    html += kv("生成方式", esc(c.generation_mode || "-"));
    html += kv("置信度", esc(c.confidence_level || "-"));
    html += "</div>";
    html += field("标题", esc(c.title || ""));
    html += field("前置条件", esc(c.precondition || "（无）"));
    html += '<div class="v2-field"><div class="v2-field-label">步骤</div>' +
        (steps.length ? '<ol class="v2-steps">' + steps.map((s) =>
            "<li>" + esc(s.action) + (s.data ? "（输入：" + esc(s.data) + "）" : "") + (s.expected ? " -> " + esc(s.expected) : "") + "</li>").join("") + "</ol>"
            : '<div class="v2-field-val v2-muted">无</div>') + "</div>";
    html += field("预期结果", esc(c.expected || ""));
    if (c.remark) html += field("备注", esc(c.remark));
    if ((c.validation_errors || []).length) {
        html += '<div class="v2-alert v2-alert-error"><b>校验错误：</b>' + c.validation_errors.map(esc).join("；") + "</div>";
    }
    el.innerHTML = html;
}

function field(label, valHtml) {
    return '<div class="v2-field"><div class="v2-field-label">' + esc(label) + '</div><div class="v2-field-val">' + valHtml + "</div></div>";
}

function renderCaseFindings(c) {
    const el = document.getElementById("caseReview");
    if (!currentReview) { el.innerHTML = '<span class="v2-muted">该 Run 无评审报告</span>'; return; }
    const f = (currentReview.findings || []).filter((x) => x.target_id === c.id);
    if (!f.length) {
        el.innerHTML = '<span class="v2-muted">本用例无相关 finding（Run 评审总分 ' + esc(currentReview.overall_score ?? "-") + "）</span>";
        return;
    }
    el.innerHTML = f.map(findingHtml).join("");
}

function renderRevisions(revs) {
    const el = document.getElementById("caseRevisions");
    if (!revs.length) { el.innerHTML = '<span class="v2-muted">无修订历史</span>'; return; }
    el.innerHTML = '<table class="v2-table"><thead><tr><th>版本</th><th>来源</th><th>修改字段</th><th>修改人</th><th>时间</th></tr></thead><tbody>' +
        revs.map((r) =>
            "<tr><td>#" + esc(r.revision_no) + "</td><td>" + provBadge(r.provenance) + "</td>" +
            "<td>" + ((r.changed_fields || []).map(esc).join(", ") || "-") + "</td>" +
            "<td>" + esc(r.changed_by || "-") + " / " + esc(r.change_source || "-") + "</td>" +
            '<td class="v2-muted">' + esc(fmtTime(r.created_at)) + "</td></tr>").join("") +
        "</tbody></table>";
}

function renderTrace(tr) {
    const el = document.getElementById("caseTrace");
    if (!tr) { el.innerHTML = '<span class="v2-muted">无追溯数据</span>'; return; }
    const items = tr.requirement_items || [], tps = tr.test_points || [];
    const node = (label, inner) => '<div class="v2-trace-node"><div class="v2-trace-label">' + esc(label) + "</div>" + inner + "</div>";
    let html = '<div class="v2-trace">';
    html += node("RequirementItem（" + items.length + "）",
        items.length ? items.map((it) => '<div class="v2-trace-card">' + esc(it.module || "") + " · " + esc(it.statement || "") + "</div>").join("") : '<span class="v2-muted">无</span>');
    html += '<div class="v2-trace-arrow">|</div>';
    html += node("TestPoint（" + tps.length + "）",
        tps.length ? tps.map((tp) => '<div class="v2-trace-card">' + provBadge(tp.provenance) + " " + esc(tp.title || "") + "</div>").join("") : '<span class="v2-muted">无</span>');
    html += '<div class="v2-trace-arrow">|</div>';
    const tc = tr.test_case || {};
    html += node("TestCase", '<div class="v2-trace-card">' + esc(tc.display_id || "") + " · " + esc(tc.title || "") + "</div>");
    html += "</div>";
    if (tr.doc || tr.version) {
        html += '<div class="v2-muted v2-small">需求文档：' + esc((tr.doc && tr.doc.title) || "-") +
            " · 版本 v" + esc((tr.version && tr.version.version_no) || "-") + "</div>";
    }
    const refs = tr.source_refs || [];
    if (refs.length) {
        html += '<div class="v2-muted v2-small">原文定位 source_ref：' +
            refs.map((r) => esc(typeof r === "string" ? r : (r.text || r.snippet || JSON.stringify(r)))).join(" | ") + "</div>";
    }
    if ((tr.issues || []).length) html += '<div class="v2-alert v2-alert-error">' + tr.issues.map(esc).join("；") + "</div>";
    el.innerHTML = html;
}

// ------------------------------------------------------------
// 阶段 A：生成（同步等待，不做 SSE / 轮询）
// ------------------------------------------------------------
async function generate() {
    if (isGenerating) { showToast("正在生成中，请勿重复点击", "error"); return; }
    const title = document.getElementById("reqTitle").value.trim();
    const text = document.getElementById("reqText").value.trim();
    if (!title || !text) { showToast("请填写需求标题和描述", "error"); return; }
    isGenerating = true;
    const btn = document.getElementById("btnGenerate");
    btn.disabled = true;
    document.getElementById("genLoading").style.display = "";
    hideError("genError");
    try {
        const r = await authFetch("/api/v2/runs", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title, text, source_type: "text" }),
        });
        const d = await r.json();
        if (!r.ok) { showError("genError", esc(d.error || "请求失败 (" + r.status + ")")); showToast("生成失败", "error"); return; }
        // P0-3：Runtime FAILED 不塌缩，醒目展示 status/failed_step/error_message
        if (d.success === false || d.status === "failed") {
            showError("genError", "<b>生成未成功</b><br>状态：" + esc(d.status || "-") +
                "<br>失败步骤：" + esc(d.failed_step || "-") +
                "<br>错误信息：" + esc(d.error_message || "-") +
                (d.run_id ? "<br>Run：" + esc(d.run_id) : ""));
            showToast("生成未完成，请查看失败详情", "error");
            await loadRuns();
            if (d.run_id) openRun(d.run_id);
            return;
        }
        showToast("生成完成！测试点 " + (d.test_point_count ?? 0) + " · 用例 " + (d.test_case_count ?? 0));
        await loadRuns();
        if (d.run_id) openRun(d.run_id);
    } catch (e) {
        showError("genError", "生成异常：" + esc(e.message));
        showToast("生成异常", "error");
    } finally {
        isGenerating = false; btn.disabled = false;
        document.getElementById("genLoading").style.display = "none";
    }
}

// ------------------------------------------------------------
// 阶段 B：编辑（乐观锁）+ 重新评审
// ------------------------------------------------------------
function openEditModal() {
    const c = currentCase;
    if (!c || !c.id || !c.title) { showToast("请先从用例列表选择一条用例", "error"); return; }
    document.getElementById("editTcId").value = c.id;
    document.getElementById("editRunId").value = currentRunId || "";
    document.getElementById("editUpdatedAt").value = c.updated_at || "";  // 乐观锁基准
    document.getElementById("editTitle").value = c.title || "";
    document.getElementById("editModule").value = c.module || "";
    document.getElementById("editPriority").value = c.priority || "P1";
    document.getElementById("editPrecondition").value = c.precondition || "";
    const stepsText = (c.steps || []).slice().sort((a, b) => a.seq - b.seq).map((s) => s.action).join("\n");
    document.getElementById("editSteps").value = stepsText;
    editStepsOriginal = stepsText;
    document.getElementById("editExpected").value = c.expected || "";
    document.getElementById("editRemark").value = c.remark || "";
    hideError("editError");
    document.getElementById("editModal").style.display = "flex";
}

function closeEditModal() { document.getElementById("editModal").style.display = "none"; }

async function saveEdit() {
    const tcId = document.getElementById("editTcId").value;
    const expected = document.getElementById("editUpdatedAt").value;
    const updates = {
        title: document.getElementById("editTitle").value.trim(),
        module: document.getElementById("editModule").value.trim(),
        priority: document.getElementById("editPriority").value,
        precondition: document.getElementById("editPrecondition").value,
        expected: document.getElementById("editExpected").value,
        remark: document.getElementById("editRemark").value,
    };
    // 仅当步骤被实际修改时才提交 steps（避免把结构化的 data/expected 子字段无意简化丢失）
    const stepsNow = document.getElementById("editSteps").value;
    if (stepsNow !== editStepsOriginal) {
        const lines = stepsNow.split("\n").map((s) => s.trim()).filter(Boolean);
        updates.steps = lines.map((a, i) => ({ seq: i + 1, action: a }));
    }
    const btn = document.getElementById("btnSaveEdit");
    btn.disabled = true;
    try {
        const r = await authFetch("/api/v2/test-cases/" + tcId + "/edit", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ updates, expected_updated_at: expected }),
        });
        const d = await r.json().catch(() => ({}));
        if (r.status === 409) {
            showError("editError", esc(d.error || "当前用例已被更新，请刷新后重新编辑"));
            showToast("编辑冲突（409），请刷新后重试", "error");
            return;
        }
        if (!r.ok) { showError("editError", esc(d.error || "保存失败 (" + r.status + ")")); return; }
        const res = d.result || {};
        if (d.success === false || res.success === false) {
            showError("editError", "保存未生效：" + esc((res.issues || res.validation_errors || []).join("；") || "未知原因"));
            return;
        }
        showToast("保存成功，状态 -> " + (res.new_status || "-") + "（需重新评审）");
        closeEditModal();
        await openRun(currentRunId);
        await openTestCase(tcId);
        await loadRuns();
    } catch (e) {
        showError("editError", "保存异常：" + esc(e.message));
    } finally {
        btn.disabled = false;
    }
}

async function reReview() {
    const c = currentCase;
    if (!c || !c.id) { showToast("请先选择用例", "error"); return; }
    const btn = document.getElementById("btnReReview");
    btn.disabled = true;
    try {
        const r = await authFetch("/api/v2/test-cases/" + c.id + "/re-review", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ run_id: currentRunId }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { showToast("重新评审失败：" + (d.error || r.status), "error"); return; }
        showToast("重新评审完成");
        await openRun(currentRunId);
        await openTestCase(c.id);
    } catch (e) {
        showToast("重新评审异常：" + e.message, "error");
    } finally {
        btn.disabled = false;
    }
}

// ------------------------------------------------------------
// 事件绑定 + 启动
// ------------------------------------------------------------
document.getElementById("btnGenerate").addEventListener("click", generate);
document.getElementById("btnRefreshRuns").addEventListener("click", loadRuns);
document.getElementById("btnLogout").addEventListener("click", doLogout);
document.getElementById("btnCloseRun").addEventListener("click", () => { document.getElementById("runDetail").style.display = "none"; });
document.getElementById("btnCloseCase").addEventListener("click", () => { document.getElementById("caseDetail").style.display = "none"; });
document.getElementById("btnEditCase").addEventListener("click", openEditModal);
document.getElementById("btnReReview").addEventListener("click", reReview);
document.getElementById("btnSaveEdit").addEventListener("click", saveEdit);
document.getElementById("btnCancelEdit").addEventListener("click", closeEditModal);
document.getElementById("btnCloseEdit").addEventListener("click", closeEditModal);
document.getElementById("editModal").addEventListener("click", (e) => { if (e.target.id === "editModal") closeEditModal(); });

checkAuth();
