// ============================================================
// 需求与 AI 分析页（P0 重排 + P4 锚点定位）
// 数据源：GET /api/v2/runs/<id>/requirements + coverage（真实端点，不伪造）。
// P4：从 Review finding「查看需求」跳入时，通过 location.state.focusItem
//     自动展开对应需求项、滚动定位并高亮 2s。
// ============================================================

import { Fragment, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { CoverageBar, Section, StatStrip } from "../components/ui";
import { useRunBundle } from "./RunBundle";
import type { RequirementItem } from "../types";

export function RequirementsTab() {
  const { requirements: req, coverage, testPoints } = useRunBundle();
  const [open, setOpen] = useState<string | null>(null);
  const loc = useLocation();
  const focusItem = (loc.state as { focusItem?: string } | null)?.focusItem;

  // P4 锚点：展开 + 滚动 + 高亮
  useEffect(() => {
    if (!focusItem) return;
    setOpen(focusItem);
    requestAnimationFrame(() => {
      const el = document.getElementById("req-item-" + focusItem);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.classList.add("item-flash");
        setTimeout(() => el.classList.remove("item-flash"), 2200);
      }
    });
  }, [focusItem, req]);

  if (!req) {
    return <div className="empty">需求 IR 数据不可用（该运行未关联需求版本，或初始化于旧版本）。</div>;
  }

  const tpCountByItem = new Map<string, number>();
  testPoints.forEach((tp) => (tp.item_ids || []).forEach((id) => tpCountByItem.set(id, (tpCountByItem.get(id) || 0) + 1)));

  const byModule = new Map<string, RequirementItem[]>();
  req.items.forEach((it) => {
    const arr = byModule.get(it.module) || [];
    arr.push(it);
    byModule.set(it.module, arr);
  });
  const low = req.items.filter((i) => i.confidence_level && i.confidence_level !== "high");

  return (
    <div>
      <Section title="AI 结构化解析产出" hint={`${req.doc?.title || "-"} · v${req.version?.version_no ?? "-"} · 来源 ${req.doc?.source_type || "-"}`}>
        <StatStrip
          items={[
            { label: "需求项", value: req.item_count, tone: "hl" },
            { label: "字段规格", value: req.field_count, sub: "驱动策略引擎" },
            { label: "业务规则", value: req.rule_count },
            { label: "权限规则", value: req.permission_count },
            { label: "模块", value: req.modules.length },
            { label: "低置信项", value: low.length, tone: low.length ? "warn" : "default", sub: low.length ? "建议人工核对" : "全部 high" },
          ]}
        />
        <div className="score-grid" style={{ marginTop: 14, maxWidth: 560 }}>
          <CoverageBar label="Strategy Obligation 覆盖" value={coverage?.strategy_obligation_coverage} />
          <CoverageBar label="Requirement Item 覆盖" value={coverage?.requirement_item_coverage} />
        </div>
        {(coverage?.uncovered_item_ids || []).length ? (
          <div className="alert alert-warn">{coverage!.uncovered_item_ids!.length} 个需求项尚未被任何测试点覆盖（见下方列表「覆盖测试点」列）。</div>
        ) : null}
        {low.length ? (
          <div className="t-aux" style={{ marginTop: 8 }}>
            低置信需求项：{low.slice(0, 5).map((i) => `${i.module} — ${i.statement.slice(0, 30)}`).join("；")}
            {low.length > 5 ? ` 等 ${low.length} 项` : ""}
          </div>
        ) : null}
      </Section>

      {[...byModule.entries()].map(([mod, items]) => (
        <Section key={mod} title={mod} hint={`${items.length} 项`}>
          <table className="table">
            <thead>
              <tr>
                <th>#</th>
                <th>类型</th>
                <th>需求描述</th>
                <th>字段/规则/权限</th>
                <th>置信度</th>
                <th>覆盖测试点</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => {
                const openRow = open === it.id;
                return (
                  <Fragment key={it.id}>
                    <tr id={"req-item-" + it.id} className="clickable" onClick={() => setOpen(openRow ? null : it.id)}>
                      <td className="col-muted">{it.seq}</td>
                      <td>{it.type}</td>
                      <td>{it.statement}</td>
                      <td className="col-muted">
                        {(it.fields || []).length} / {(it.rules || []).length} / {(it.permissions || []).length}
                      </td>
                      <td>{it.confidence_level || "-"}</td>
                      <td>{tpCountByItem.get(it.id) || 0}</td>
                    </tr>
                    {openRow ? (
                      <tr>
                        <td colSpan={6} style={{ background: "var(--bg-soft)" }}>
                          <ItemDetail item={it} />
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </Section>
      ))}
    </div>
  );
}

function ItemDetail({ item }: { item: RequirementItem }) {
  return (
    <div style={{ padding: "6px 4px" }}>
      {(item.fields || []).length ? (
        <>
          <div className="t-section" style={{ margin: "8px 0 6px" }}>字段规格（FieldSpec → 策略引擎输入）</div>
          <table className="table">
            <thead>
              <tr>
                <th>字段</th>
                <th>名称</th>
                <th>类型</th>
                <th>约束</th>
                <th>枚举</th>
                <th>示例</th>
              </tr>
            </thead>
            <tbody>
              {item.fields!.map((f) => (
                <tr key={f.name}>
                  <td>{f.name}</td>
                  <td>{f.label || "-"}</td>
                  <td>{f.data_type}</td>
                  <td className="col-muted">
                    {[
                      f.required ? "必填" : null,
                      f.min_length != null ? `len≥${f.min_length}` : null,
                      f.max_length != null ? `len≤${f.max_length}` : null,
                      f.min_value != null ? `≥${f.min_value}` : null,
                      f.max_value != null ? `≤${f.max_value}` : null,
                      f.pattern ? "regex" : null,
                      f.unique ? "唯一" : null,
                    ]
                      .filter(Boolean)
                      .join(" · ") || "-"}
                  </td>
                  <td className="col-muted">{(f.enum_values || []).join(",") || "-"}</td>
                  <td className="col-muted">{f.example || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
      {(item.rules || []).length ? (
        <>
          <div className="t-section" style={{ margin: "8px 0 6px" }}>业务规则</div>
          {item.rules!.map((r, i) => (
            <div key={i} className="trace-card">
              <b>{r.name}</b>（{r.expression_type}）：{r.expression}
              {r.expected ? ` → ${r.expected}` : ""}
            </div>
          ))}
        </>
      ) : null}
      {(item.permissions || []).length ? (
        <>
          <div className="t-section" style={{ margin: "8px 0 6px" }}>权限规则</div>
          {item.permissions!.map((p, i) => (
            <div key={i} className="trace-card">
              {p.role} × {p.resource} × {p.action} → {p.allowed ? "允许" : "拒绝"}
              {p.condition ? `（条件：${p.condition}）` : ""}
            </div>
          ))}
        </>
      ) : null}
      {(item.acceptance_criteria || []).length ? (
        <>
          <div className="t-section" style={{ margin: "8px 0 6px" }}>验收标准</div>
          <ol className="steps">
            {item.acceptance_criteria!.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ol>
        </>
      ) : null}
      {item.source_ref?.value ? (
        <div className="t-aux" style={{ marginTop: 8 }}>
          原文定位（{item.source_ref.locator}）：{item.source_ref.value}
        </div>
      ) : null}
    </div>
  );
}
