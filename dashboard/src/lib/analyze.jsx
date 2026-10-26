import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "./api.js";
import { Banner, Button, Modal, useToast } from "../ui.jsx";

/**
 * Start an analysis from anywhere. Repositories that are not trusted yet get a confirmation
 * dialog first: analysing one runs its tests (and mutants of its code) on this machine.
 */
export function useAnalyze({ onStarted } = {}) {
  const navigate = useNavigate();
  const toast = useToast();
  const [pending, setPending] = useState(null); // { repo, ref } waiting for trust
  const [busy, setBusy] = useState(null);

  const start = useCallback(
    async (repo, ref, { llm = true, publish = false } = {}) => {
      const key = `${repo.id}:${ref}`;
      setBusy(key);
      try {
        const run = await api("/analyses", { method: "POST", body: { repo_id: repo.id, ref: String(ref), llm, publish } });
        toast.push(run.deduplicated ? "This pull request is already being analysed." : "Analysis started.", "success");
        onStarted?.(run);
        navigate(`/runs/${run.run_id}`);
      } catch (error) {
        if (error.code === "not_trusted") setPending({ repo, ref, llm });
        else toast.push(error.message, "error");
      } finally {
        setBusy(null);
      }
    },
    [navigate, toast, onStarted],
  );

  const dialog = pending && <TrustDialog pending={pending} onClose={() => setPending(null)} onTrusted={() => { const p = pending; setPending(null); start({ ...p.repo, trusted: true }, p.ref, { llm: p.llm }); }} />;
  return { start, busy, dialog };
}

export function TrustDialog({ pending, onClose, onTrusted }) {
  const toast = useToast();
  const [saving, setSaving] = useState(false);
  const [sure, setSure] = useState(false);
  const confirm = async () => {
    setSaving(true);
    try {
      await api(`/repos/${pending.repo.id}/trust`, { method: "POST", body: { trusted: true } });
      onTrusted();
    } catch (error) {
      toast.push(error.message, "error");
    } finally {
      setSaving(false);
    }
  };
  return (
    <Modal
      open title={`Run ${pending.repo.full_name} on this machine, unsandboxed?`} onClose={onClose}
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!sure} loading={saving} onClick={confirm}>Trust and analyse</Button></>}
    >
      <Banner tone="warn" title="Docker is not available, so there is no sandbox">
        Normally SentinelPR runs a repository's tests and mutants in a locked-down Docker container. Docker is not running, so the only way to analyse this pull request
        is directly on this computer: a subprocess with a timeout, but no container, no network restriction and no filesystem isolation. Starting Docker and trying again is safer.
      </Banner>
      <label className="flex cursor-pointer items-start gap-2.5 text-muted">
        <input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} className="mt-0.5 h-4 w-4 accent-[var(--color-brand)]" />
        I trust the code in {pending.repo.full_name} (including pull request branches from forks) to run on this computer without a sandbox.
      </label>
    </Modal>
  );
}
