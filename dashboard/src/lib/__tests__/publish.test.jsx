import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import PublishPanel from "../../features/run/PublishPanel.jsx";
import { ToastProvider } from "../../ui.jsx";

const json = (body, status = 200) => Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
const TARGET = { repository: "gmayank9999/sentinelpr-integration-lab", pr_number: 2, head_sha: "abcdef1234567890", decision: "PASS" };
const WILL = { comment: "A pull request comment with the decision.", status: "A commit status on the analysed commit.", check_run: "A Check Run, only through a GitHub App." };
const NO_APP = { available: false, via: null, needs: "A GitHub App with Checks: Read and write", problems: ["No GitHub App is configured. GitHub only allows a GitHub App to create Check Runs; a personal access token cannot."] };
const WITH_APP = { available: true, via: "GitHub App", app: { name: "SentinelPR Gate", slug: "sentinelpr-gate" }, problems: [] };
const targets = (check = NO_APP) => ({ comment: { available: true, needs: "Pull requests: Read and write" }, status: { available: true, needs: "Commit statuses: Read and write", context: "SentinelPR / gate" }, check_run: check });
const info = (over = {}) => ({ run_id: "r", allowed: true, blockers: [], target: TARGET, targets: targets(), will_post: WILL, stale: null, already_published: null, history: [], ...over });
const ok = { posted: true, attempted: true, id: 9, url: "https://github.com/o/r/pull/2#issuecomment-9", error: null };
const result = (over = {}) => ({
  published: true, repository: TARGET.repository, pr_number: 2, head_sha: TARGET.head_sha, comment: ok, status: { posted: true, attempted: true, id: 4, error: null },
  check_run: { created: false, attempted: false, id: null, error: null, explanation: "Not attempted: creating a Check Run needs a GitHub App, and none is configured. GitHub does not let a personal access token create one.",
    fix: "Set up a GitHub App (docs/github-app.md), or use the commit status, which a personal token can post and which can be a required check." }, ...over,
});

function mount(fetchImpl) {
  vi.stubGlobal("fetch", vi.fn(fetchImpl));
  return render(<ToastProvider><PublishPanel runId="r" meta={{ status: "completed" }} /></ToastProvider>);
}
const open = () => fireEvent.click(screen.getByRole("button", { name: /publish to github/i }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("PublishPanel", () => {
  it("is always clickable; when publishing is off it says why and exactly how to fix it, and cannot post", async () => {
    mount(() => json(info({ allowed: false, blockers: [
      { code: "disabled", message: "Publishing is switched off on this server (a safety default).", fix: "Add the line SENTINEL_ALLOW_PUBLISH=1 to the .env file, then restart the server." }] })));
    const button = screen.getByRole("button", { name: /publish to github/i });
    expect(button.disabled).toBe(false); // never a dead control
    fireEvent.click(button);
    expect(await screen.findByText(/switched off on this server/)).toBeTruthy();
    expect(screen.getByText(/Add the line SENTINEL_ALLOW_PUBLISH=1 to the \.env file/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Post to GitHub" }).disabled).toBe(true);
    expect(screen.getByRole("button", { name: "Check again" })).toBeTruthy();
  });

  it("names the exact repository, pull request and commit before anything is posted", async () => {
    mount(() => json(info()));
    open();
    expect(await screen.findByText("gmayank9999/sentinelpr-integration-lab")).toBeTruthy();
    expect(screen.getByText("#2")).toBeTruthy();
    expect(screen.getByText("abcdef1")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Post to GitHub" }).disabled).toBe(false);
  });

  it("offers the comment and the commit status, and says truthfully that a Check Run needs a GitHub App", async () => {
    mount(() => json(info()));
    open();
    expect(await screen.findByText("Pull request comment")).toBeTruthy();
    expect(screen.getByText(/Commit status “SentinelPR \/ gate”/)).toBeTruthy();
    const check = screen.getByRole("checkbox", { name: /Check Run/ });
    expect(check.disabled).toBe(true);
    expect(check.checked).toBe(false);
    expect(screen.getByText(/GitHub only lets a GitHub App create Check Runs; a personal access token cannot/)).toBeTruthy();
    expect(screen.getByText(/docs\/github-app\.md/)).toBeTruthy();
    expect(screen.queryByText(/Checks: Read and write on the token/)).toBeNull(); // the earlier wrong advice (grant Checks to the PAT) is gone
  });

  it("offers the Check Run when an App is installed and says which App creates it", async () => {
    mount(() => json(info({ targets: targets(WITH_APP) })));
    open();
    const check = await screen.findByRole("checkbox", { name: /Check Run/ });
    expect(check.disabled).toBe(false);
    expect(check.checked).toBe(true);
    expect(screen.getByText("SentinelPR Gate")).toBeTruthy();
  });

  it("posts once even if clicked twice, sending exactly the selected targets, then reports each outcome separately", async () => {
    const posts = [];
    let release;
    const gate = new Promise((r) => { release = r; });
    mount((url, opts) => {
      if (opts?.method === "POST") { posts.push(JSON.parse(opts.body)); return gate.then(() => json(result())); }
      return json(info());
    });
    open();
    const post = await screen.findByRole("button", { name: "Post to GitHub" });
    await waitFor(() => expect(post.disabled).toBe(false));
    fireEvent.click(screen.getByRole("checkbox", { name: /Commit status/ })); // untick the status
    fireEvent.click(post);
    fireEvent.click(post);
    fireEvent.click(post);
    expect(posts).toHaveLength(1); // duplicates are swallowed while the request is in flight
    expect(posts[0]).toEqual({ force: false, targets: { comment: true, status: false, check_run: false } });
    release();
    expect(await screen.findByText("Pull request comment: posted")).toBeTruthy();
    expect(screen.getByText("Commit status: posted")).toBeTruthy();
    expect(screen.getByText("Check Run: not attempted")).toBeTruthy(); // not attempted is not the same as refused
    expect(screen.queryByText("Check Run: created")).toBeNull();
    expect(screen.queryByText("Check Run: refused by GitHub")).toBeNull();
    expect(screen.getByText(/needs a GitHub App, and none is configured/)).toBeTruthy();
    expect(screen.getByRole("link", { name: /Open the comment/ }).getAttribute("href")).toContain("issuecomment-9");
  });

  it("shows a Check Run as created only when GitHub returned its id", async () => {
    mount((url, opts) => (opts?.method === "POST" ? json(result({ check_run: { created: true, attempted: true, id: 77, error: null, explanation: null } })) : json(info({ targets: targets(WITH_APP) }))));
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Post to GitHub" }));
    expect(await screen.findByText("Check Run: created")).toBeTruthy();
    expect(screen.getByText(/check run id 77/)).toBeTruthy();
  });

  it("distinguishes a Check Run GitHub refused (with GitHub's own words) from one that was never attempted", async () => {
    mount((url, opts) => (opts?.method === "POST"
      ? json(result({ check_run: { created: false, attempted: true, id: null, error: 403, github_message: "Resource not accessible by integration", explanation: "GitHub refused the Check Run (403): the GitHub App installation lacks the permission.",
        fix: "Open the App's settings → Permissions & events → set the permission to Read and write." } }))
      : json(info({ targets: targets(WITH_APP) }))));
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Post to GitHub" }));
    expect(await screen.findByText("Check Run: refused by GitHub")).toBeTruthy();
    expect(screen.getByText(/Resource not accessible by integration/)).toBeTruthy();
    expect(screen.getByText(/What to do: Open the App's settings/)).toBeTruthy();
  });

  it("reports total failure honestly and keeps the error visible", async () => {
    mount((url, opts) => (opts?.method === "POST"
      ? json(result({ published: false, comment: { posted: false, attempted: true, id: null, error: 403, explanation: "GitHub refused the pull request comment (403): this token does not have the permission it requires.", fix: "Open GitHub → your token → Pull requests → Read and write → Update." },
        status: { posted: false, attempted: true, id: null, error: 403, explanation: "GitHub refused the commit status (403): this token does not have the permission it requires.", fix: "Open GitHub → your token → Commit statuses → Read and write → Update." } }))
      : json(info())));
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Post to GitHub" }));
    expect(await screen.findByText("Nothing was published")).toBeTruthy();
    expect(screen.getByText("Pull request comment: not posted")).toBeTruthy();
    expect(screen.getByText("Commit status: not posted")).toBeTruthy();
    expect(screen.getByText(/Commit statuses → Read and write/)).toBeTruthy();
  });

  it("surfaces a server error instead of swallowing it, and re-enables the button", async () => {
    mount((url, opts) => (opts?.method === "POST" ? json({ error: { code: "publish_disabled", message: "Publishing is switched off." } }, 403) : json(info())));
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Post to GitHub" }));
    expect(await screen.findByText("Publishing failed")).toBeTruthy();
    await waitFor(() => expect(screen.getByRole("button", { name: "Post to GitHub" }).disabled).toBe(false));
  });

  it("cannot post with nothing selected", async () => {
    mount(() => json(info()));
    open();
    await screen.findByText("Pull request comment");
    fireEvent.click(screen.getByRole("checkbox", { name: /Pull request comment/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Commit status/ }));
    expect(screen.getByText(/Select at least one thing to post/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Post to GitHub" }).disabled).toBe(true);
  });

  it("requires explicit confirmation to publish a result that was already published", async () => {
    const posts = [];
    mount((url, opts) => {
      if (opts?.method === "POST") { posts.push(JSON.parse(opts.body)); return json(result()); }
      return json(info({ already_published: { comment: { posted: true, id: 3 } } }));
    });
    open();
    const post = await screen.findByRole("button", { name: "Post to GitHub" });
    expect(post.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: /already published/ }));
    expect(screen.getByRole("button", { name: "Post to GitHub" }).disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Post to GitHub" }));
    await screen.findByText("Pull request comment: posted");
    expect(posts[0].force).toBe(true);
  });

  it("warns when the pull request has moved on since the analysis", async () => {
    mount(() => json(info({ stale: true })));
    open();
    expect(await screen.findByText(/has changed since this analysis/)).toBeTruthy();
  });
});
