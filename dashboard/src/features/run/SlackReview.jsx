import { useState } from "react";
import { api, useApi } from "../../lib/api.js";
import { Button, Card, KV, useToast } from "../../ui.jsx";

const DELIVERY = { not_sent: "Not sent", sending: "Sending", sent: "Sent", failed: "Failed", uncertain: "Unconfirmed", skipped: "Skipped" };
const REVIEW = { pending: "Pending", processing: "Checking GitHub", approved: "Approved", changes_requested: "Changes Requested", outdated: "Outdated" };

export default function SlackReview({ runId }) {
  const path = `/analyses/${encodeURIComponent(runId)}/slack`;
  const status = useApi(path, { interval: 5000 });
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const data = status.data;
  if (!data || data.delivery === "disabled") return null;
  const review = data.review;
  const resend = async () => {
    setBusy(true);
    try {
      const result = await api(path, { method: "POST" });
      toast.push(result.message_update_error || result.delivery_error || (result.delivery === "sent" ? "Slack message is up to date." : "Slack did not confirm delivery."),
        result.delivery === "sent" && !result.message_update_error ? "success" : "error");
      status.reload();
    } catch (error) {
      toast.push(error.message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card className="mb-4" title="Slack review" action={data.can_resend && ["not_sent", "failed", "sent"].includes(data.delivery)
      ? <Button size="sm" loading={busy} onClick={resend}>{data.delivery === "sent" ? "Refresh Slack message" : "Resend to Slack"}</Button> : null}>
      <KV rows={[
        ["Notification", DELIVERY[data.delivery] || data.delivery],
        data.delivery_error && ["Delivery detail", data.delivery_error],
        data.message_update_error && ["Message update", data.message_update_error],
        review && ["Human review", REVIEW[review.status] || review.status],
        review?.reviewer && ["Slack reviewer", review.reviewer_name ? `${review.reviewer_name} (${review.reviewer})` : review.reviewer],
        review?.reviewed_at && ["Reviewed at", new Date(review.reviewed_at).toLocaleString()],
        review?.comment && [review.decision === "changes_requested" ? "Rejection reason" : "Approval comment", review.comment],
        review?.sync && ["GitHub synchronization", [review.sync, review.sync_detail].filter(Boolean).join(" · ")],
      ]} />
    </Card>
  );
}
