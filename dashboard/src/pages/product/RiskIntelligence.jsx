import { Link } from "react-router-dom";
import { useApi } from "../../lib/api.js";
import { prLabel } from "../../lib/format.js";
import { Async, Card, DecisionBadge, EmptyState, PageHeader, SkeletonRows, cx } from "../../ui.jsx";
import ModelCard, { WeightsTable } from "../../features/run/ModelCard.jsx";

const BAR = { PASS: "bg-ok/60", CANARY: "bg-warn/60", BLOCK: "bg-bad/60" };

export default function RiskIntelligence() {
  const runs = useApi("/analyses?status=completed&limit=40");
  return (
    <div>
      <PageHeader title="Risk Intelligence" subtitle="How risk is computed, how the model was built and evaluated, and where your recent pull requests fall." />
      <div className="grid gap-6 xl:grid-cols-5">
        <div className="space-y-6 xl:col-span-3">
          <Card title="Recent pull requests by calibrated risk">
            <Async state={runs} skeleton={<SkeletonRows rows={3} />} empty={<EmptyState title="No completed analyses">Risk scores appear here after the first analysis.</EmptyState>}>
              {(list) => (
                <ul className="space-y-2">
                  {[...list].filter((r) => r.risk != null).sort((a, b) => b.risk - a.risk).map((r) => (
                    <li key={r.run_id}><Link to={`/runs/${r.run_id}?tab=risk`} className="group grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1">
                      <span className="truncate text-sm group-hover:underline">{r.pr.title || prLabel(r.pr)} <span className="text-faint">· {prLabel(r.pr)}</span></span>
                      <DecisionBadge decision={r.decision} />
                      <span className="col-span-2 flex items-center gap-2"><span className="h-1.5 flex-1 rounded bg-s3"><span className={cx("block h-1.5 rounded", BAR[r.decision])} style={{ width: `${r.risk * 100}%` }} /></span><span className="mono w-10 text-right text-xs text-muted">{r.risk.toFixed(2)}</span></span>
                    </Link></li>))}
                </ul>
              )}
            </Async>
          </Card>
          <Card title="Model weights" pad><p className="mb-3 text-xs text-muted">Weight per raw unit of each feature, as stored in the model file. A positive weight raises the log-odds of a defect.</p><WeightsTable /></Card>
        </div>
        <div className="xl:col-span-2"><Card title="Model card"><ModelCard /></Card></div>
      </div>
    </div>
  );
}
