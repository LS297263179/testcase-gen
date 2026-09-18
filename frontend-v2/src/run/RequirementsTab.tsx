// ============================================================
// 需求与 AI 分析页：展示 Requirement IR（Step 2 结构化解析结果）。
// 数据源：GET /api/v2/runs/<id>/requirements（本轮新增只读端点）+ coverage。
// 「AI 做了什么」第一层答案：把原始需求解析成了多少需求项/字段/规则/权限。
// ============================================================

import { Fragment, useState } from "react";
import { Card, CoverageBar, EmptyState } from "../components/ui";
import { useRunBundle } from "./RunBundle";
import type { RequirementItem } from "../types";

export function RequirementsTab() {
  const { requirements: req, coverage, testPoints } = useRunBundle();
  const [open, setOpen] = useState<string | null>(null);

  if (!req) {
    return <EmptyState text="需求 IR 数据不可用（该运行未关联需求版本，或初始化于旧版本）。" />;
  }

  // 需求项 → 测试点数量映射（test_point.item_ids 反查）
  const tpCountByItem = new Map<string, number>();
  testPoints.forEach((tp) => (tp.item_ids || []).forEach((id) => tpCountByItem.set(id, (tpCountByItem.get(id) || 0) + 1)));

  const byModule = new Map<string, RequirementItem[]>();
  req.items.forEach((it) => {
    const arr = byModule.get(it.module) || [];
    arr.push(it);
    byModule.set(it.module, arr);
  });

  return (
    <div>
      <div className="grid-4" style={{ marginBottom: 16 }}>
        <Card>
          <div className="kv-grid">
            <div className="kv">
              <span className="k">需求文档</span>
              <span className="v">{req.doc?.title || "-"}</span>
            </div>
            <div className="kv">
              <span className="k">版本</span>
              <span className="v">v{req.version?.version_no ?? "-"}</span>
            </div>
            <div className="kv">
              <span className="k">来源类型</span>
              <span className="v">{req.doc?.source_type || "-"}</span>
            </div>
          </div>
        </Card>
        <Card title="AI 结构化解析产出">
          <div className="kv-grid">
            <div className="kv">
              <span className="k">需求项</span>
              <span className="v">{req.item_count}</span>
            </div>
            <div className="kv">
              <span className="k">字段规格</span>
              <span className="v">{req.field_count}</span>
            </div>
            <div className="kv">
              <span className="k">业务规则</span>
              <span className="v">{req.rule_count}</span>
            </div>
            <div className="kv">
              <span className="k">权限规则</span>
              <span className="v">{req.permission_count}</span>
            </div>
            <div className="kv">
              <span className="k">模块数</span>
              <span className="v">{req.modules.length}</span>
            </div>
          </div>
        </Card>
        <Card title="覆盖情况">
          <div className="score-grid">
            <CoverageBar label="Strategy Obligation" value={coverage?.strategy_obligation_coverage} />
            <CoverageBar label="Requirement Item" value={coverage?.requirement_item_coverage} />
          </div>
          {(coverage?.uncovered_item_ids || []).length ? (
            <div className="alert alert-warn small">存在 {coverage!.uncovered_item_ids!.length} 个未被测试点覆盖的需求项。</div>
          ) : (
            <div className="muted small">无未覆盖需求项。</div>
          )}
        </Card>
        <Card title="低置信提示">
          {(() => {
            const low = req.items.filter((i) => i.confidence_level && i.confidence_level !== "high");
            return low.length ? (
              <div className="alert alert-warn small">
                {low.length} 个需求项置信度非 high，建议人工核对：
                {low.slice(0, 5).map((i) => (
                  <div key={i.id}>
                    · {i.module} — {i.statement.slice(0, 40)}
                  </div>
                ))}
              </div>
            ) : (
              <div className="muted small">所有需求项置信度为 high。</div>
            );
          })()}
        </Card>
      </div>

      {[...byModule.entries()].map(([mod, items]) => (
        <Card key={mod} title={`${mod}（${items.length} 项）`}>
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
                    <tr className="clickable" onClick={() => setOpen(openRow ? null : it.id)}>
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
                        <td colSpan={6} style={{ background: "var(--bg-elevated)" }}>
                          <ItemDetail item={it} />
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </Card>
      ))}
    </div>
  );
}

function ItemDetail({ item }: { item: RequirementItem }) {
  return (
    <div style={{ padding: "6px 4px" }}>
      {(item.fields || []).length ? (
        <>
          <div className="section-title">字段规格（FieldSpec → 策略引擎输入）</div>
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
          <div className="section-title">业务规则</div>
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
          <div className="section-title">权限规则</div>
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
          <div className="section-title">验收标准</div>
          <ul className="steps">
            {item.acceptance_criteria!.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </>
      ) : null}
      {item.source_ref?.value ? (
        <div className="muted small" style={{ marginTop: 8 }}>
          原文定位（{item.source_ref.locator}）：{item.source_ref.value}
        </div>
      ) : null}
    </div>
  );
}
