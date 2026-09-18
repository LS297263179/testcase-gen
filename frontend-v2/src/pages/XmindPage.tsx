// ============================================================
// 资源 · XMind 与迁移：辅助能力定位页（不伪装 V2 能力）。
// XMind 转用例 / V1 数据迁移均在 V1 与 CLI 中完成，产物归属如实说明。
// ============================================================

import { Card } from "../components/ui";
import { Shell } from "../layout/Shell";

export function XmindPage() {
  return (
    <Shell crumbs={<><span>资源</span> <span>/</span> <b>XMind 与迁移</b></>}>
      <div className="page" style={{ maxWidth: 860 }}>
        <div className="page-header">
          <div className="ph-main">
            <h2>XMind 与迁移</h2>
            <div className="ph-desc">导入 / 迁移属于辅助工具，不在 V2 测试设计主线上。</div>
          </div>
        </div>

        <Card title="XMind 转测试用例（V1 能力）">
          <p className="small">
            XMind 文件转换在 V1 工作台完成，产物进入 <b>V1 会话资产库</b>（data.db），不会自动出现在 V2 测试运行中。
          </p>
          <a className="btn btn-outline" href="/#page=xmind2case">
            前往 V1 使用 XMind 转用例 →
          </a>
        </Card>

        <Card title="V1 历史数据迁移到 V2（CLI）">
          <p className="small">
            迁移遵循「不猜历史关系、未知=NULL、provenance=migrated」铁律，由后端脚本执行（真实 data.db 实测 106 用例零丢失）：
          </p>
          <pre className="field-block" style={{ background: "var(--bg-elevated)", padding: 10, borderRadius: 6, fontSize: 12 }}>
            python -m core.v2.migrate_v1_to_v2
          </pre>
          <div className="muted small">迁移进来的用例在 V2 运行中以「MIGRATED」来源标识呈现，不伪造测试点关联。</div>
        </Card>
      </div>
    </Shell>
  );
}
