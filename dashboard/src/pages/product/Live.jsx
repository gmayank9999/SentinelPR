import { Link, Navigate } from "react-router-dom";
import { Radio } from "lucide-react";
import { useApi } from "../../lib/api.js";
import { Button, EmptyState, ErrorState, SkeletonRows, useMode } from "../../ui.jsx";
import { useEffect } from "react";

/** Live Execution entry point: the run in progress, else the most recent one, shown in Live mode. */
export default function Live() {
  const { setMode } = useMode();
  const runs = useApi("/analyses?limit=20");
  useEffect(() => setMode("live"), [setMode]);
  if (runs.loading) return <SkeletonRows rows={3} />;
  if (runs.error) return <ErrorState error={runs.error} onRetry={runs.refetch} />;
  const list = runs.data || [];
  const target = list.find((r) => r.status === "running") || list.find((r) => r.status === "queued") || list[0];
  if (target) return <Navigate to={`/runs/${target.run_id}`} replace />;
  return (
    <EmptyState icon={Radio} title="Nothing has run yet" action={<Link to="/pulls"><Button variant="primary">Choose a pull request</Button></Link>}>
      Start an analysis and watch the pipeline execute here, stage by stage.
    </EmptyState>
  );
}
