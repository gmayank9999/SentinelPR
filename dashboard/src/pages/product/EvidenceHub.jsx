import { useEffect, useState } from "react";
import { ScanSearch } from "lucide-react";
import { useApi } from "../../lib/api.js";
import { prLabel, stamp } from "../../lib/format.js";
import { Async, EmptyState, ErrorState, PageHeader, SkeletonRows } from "../../ui.jsx";
import EvidenceExplorer from "../../features/run/EvidenceExplorer.jsx";

export default function EvidenceHub() {
  const runs = useApi("/analyses?status=completed&limit=50");
  const [runId, setRunId] = useState("");
  useEffect(() => { if (!runId && runs.data?.length) setRunId(runs.data[0].run_id); }, [runs.data, runId]);
  const detail = useApi(runId ? `/analyses/${encodeURIComponent(runId)}` : null);
  return (
    <div>
      <PageHeader title="Verification Evidence" subtitle="Every claim an agent made about a change, and whether execution confirmed, contradicted or could not decide it."
        actions={runs.data?.length ? (
          <select aria-label="Choose analysis" value={runId} onChange={(e) => setRunId(e.target.value)} className="h-9 max-w-xs rounded-md border border-line2 bg-bg px-2 text-sm">
            {runs.data.map((r) => <option key={r.run_id} value={r.run_id}>{prLabel(r.pr)} {r.pr.title || ""} · {stamp(r.created_at)} · {r.decision}</option>)}
          </select>) : null} />
      <Async state={runs} skeleton={<SkeletonRows />} empty={<EmptyState icon={ScanSearch} title="No completed analyses yet">Claims and their verdicts appear after the first analysis.</EmptyState>}>
        {() => detail.loading ? <SkeletonRows /> : detail.error ? <ErrorState error={detail.error} onRetry={detail.refetch} /> : detail.data?.report ? <EvidenceExplorer key={runId} report={detail.data.report} /> : null}
      </Async>
    </div>
  );
}
