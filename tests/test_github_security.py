"""GitHub integration: what it accepts, how it fails, and that credentials never leak."""

import base64
import json
import subprocess

import httpx
import pytest

from sentinel.config import Config
from sentinel.service import github
from sentinel.service.events import EventLog
from sentinel.service.github import GitHubClient, GitHubError
from sentinel.service.repos import RepoError, RepoRegistry
from sentinel.service.runs import publish_report

TOKEN = "ghp_" + "A1b2C3d4" * 5


# --- what a repository URL may be ------------------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "file://localhost/o/r", "ssh://git@github.com/o/r", "git@github.com:o/r.git", "git://github.com/o/r", "ftp://github.com/o/r",
    "http://github.com/o/r", "https://github.com.evil.example/o/r", "https://evil.example/github.com/o/r", "https://user:pw@github.com/o/r",
    "https://github.com:8080/o/r", "https://169.254.169.254/o/r", "https://localhost/o/r", "https://127.0.0.1/o/r", "https://[::1]/o/r", "https://0.0.0.0/o/r",
    "https://github.com/o/r?token=x", "https://github.com/o/r#frag", "https://github.com/o/r/pull/1", "https://github.com/../etc/passwd", "https://github.com/o/..",
    "https://githuб.com/o/r", "https://github.com/o/r;touch x", "https://github.com/o/r$(id)", "https://github.com/o/r\nHost: evil", "  ", "", "o/r/x/y", "-o/r",
    "https://github.com/-evil/r", "o/" + "r" * 101,
])
def test_unsafe_or_malformed_repository_urls_are_rejected(url):
    with pytest.raises(GitHubError) as err:
        github.parse_repo_url(url)
    assert err.value.code == "invalid_repository" and err.value.status == 422


def test_only_the_expected_shapes_are_accepted():
    assert github.parse_repo_url("https://github.com/psf/requests") == ("psf", "requests")
    assert github.parse_repo_url("github.com/psf/requests.git") == ("psf", "requests")
    assert github.parse_repo_url("psf/requests") == ("psf", "requests")


@pytest.mark.parametrize("ref", ["--upload-pack=touch /tmp/x", "-rf", "a..b", "a b", "x;y", "refs/heads/../../x", "", "a" * 201, "x\ny", "$(id)", "main.lock"])
def test_branch_names_are_checked_before_they_reach_git(ref):
    assert not github.valid_ref(ref)
    with pytest.raises(GitHubError):
        github.fetch_pull(__import__("pathlib").Path("."), 1, ref)


def test_repository_ids_cannot_escape_the_service_directory(tmp_path):
    registry = RepoRegistry(Config.load(tmp_path, environ={}, overrides={"workdir": str(tmp_path / "w")}), tmp_path / "svc")
    for bad in ("../x", "..", "a/../../b", "a\\b", "/etc", "UPPER", ""):
        with pytest.raises(RepoError):
            registry.get(bad)
    assert str(registry.checkout_path("acme--widgets")).startswith(str((tmp_path / "svc" / "repos").resolve()))


# --- failure modes ---------------------------------------------------------------------------------------------------------


def client(handler):
    return GitHubClient(httpx.Client(base_url=github.API, transport=httpx.MockTransport(handler)))


def test_redirects_are_not_followed(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(301, headers={"location": "https://evil.example/steal"})

    with pytest.raises(GitHubError) as err:
        client(handler).repo("o", "r")
    assert err.value.code == "moved" and seen == ["https://api.github.com/repos/o/r"]  # one request, never the redirect target


def test_rate_limit_is_reported_with_guidance(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("SENTINEL_GITHUB_TOKEN", raising=False)
    handler = lambda request: httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1893456000"})  # noqa: E731
    with pytest.raises(GitHubError) as err:
        client(handler).repo("o", "r")
    assert err.value.code == "rate_limited" and err.value.status == 429 and "GITHUB_TOKEN" in str(err.value)


def test_private_or_missing_repositories_give_the_same_answer(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(GitHubError) as err:
        client(lambda r: httpx.Response(404)).repo("o", "r")
    assert err.value.code == "not_found" and "GITHUB_TOKEN" in str(err.value)  # it does not reveal whether the repository exists


def test_transient_failures_are_retried_then_reported(monkeypatch):
    monkeypatch.setattr(github.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"owner": {"login": "o"}, "name": "r", "full_name": "o/r", "private": False, "default_branch": "main", "html_url": "u", "clone_url": "c"})

    assert client(flaky).repo("o", "r")["full_name"] == "o/r" and calls["n"] == 3

    def down(request):
        raise httpx.ConnectTimeout("slow")

    with pytest.raises(GitHubError) as err:
        client(down).repo("o", "r")
    assert err.value.code == "unreachable" and err.value.status == 503


# --- credentials never leak -----------------------------------------------------------------------------------------------------


def test_the_token_is_not_on_the_git_command_line_or_inherited_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "k" * 40)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"], captured["env"] = cmd, kwargs["env"]
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(github.subprocess, "run", fake_run)
    github.run_git(["fetch", "origin"], tmp_path)
    argv = " ".join(captured["cmd"])
    basic = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    assert TOKEN not in argv and basic not in argv and "Authorization" not in argv
    assert basic in captured["env"]["GIT_CONFIG_VALUE_0"]  # delivered through the environment instead
    assert "GITHUB_TOKEN" not in captured["env"] and "GROQ_API_KEY" not in captured["env"]
    for flag in ("protocol.allow=never", "protocol.https.allow=always", "core.symlinks=false", "submodule.recurse=false"):
        assert flag in argv


def test_tokens_are_scrubbed_from_git_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(github.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 128, "", f"fatal: unable to access with {TOKEN}"))
    with pytest.raises(GitHubError) as err:
        github.run_git(["fetch"], tmp_path)
    assert TOKEN not in str(err.value) and "REDACTED" in str(err.value)


def test_the_api_never_returns_the_token(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from sentinel.api import create_api

    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "k" * 40)
    api = TestClient(create_api(Config.load(tmp_path, environ={}, overrides={"workdir": str(tmp_path / "w")})))
    blob = "".join(api.get(path).text for path in ("/api/service/status", "/api/repos", "/api/llm/status", "/api/overview"))
    assert TOKEN not in blob and "gsk_" not in blob
    assert json.loads(api.get("/api/service/status").text)["github"] == {"authenticated": True}


# --- publishing is opt-in, gated and honest -----------------------------------------------------------------------------------------


class Spec:
    class pr:  # noqa: N801
        is_fork = False

    head = "a" * 40
    cfg = None


def publish(tmp_path, monkeypatch, *, enabled=True, token=TOKEN, fork=False, fn=None, kind="github", number=7):
    for name, value in (("SENTINEL_ALLOW_PUBLISH", "1" if enabled else ""), ("GITHUB_TOKEN", token or ""), ("SENTINEL_ALLOW_PUBLISH_FORKS", "")):
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SENTINEL_GITHUB_TOKEN", raising=False)
    spec = Spec()
    spec.pr = type("P", (), {"is_fork": fork})
    log = EventLog("r", tmp_path / "e.jsonl")
    out = publish_report(log, {"decision": "PASS"}, spec, {"pr": {"number": number}}, {"kind": kind, "full_name": "o/r"}, publish_fn=fn)
    return out, log


def test_nothing_is_published_unless_the_server_allows_it(tmp_path, monkeypatch):
    called = []
    out, log = publish(tmp_path, monkeypatch, enabled=False, fn=lambda *a, **k: called.append(1))
    assert out["published"] is False and "SENTINEL_ALLOW_PUBLISH" in out["reason"] and not called
    assert [e["kind"] for e in log.since(0)] == ["stage_started", "stage_skipped"]


@pytest.mark.parametrize("kwargs,fragment", [({"token": None}, "No GITHUB_TOKEN"), ({"fork": True}, "fork"), ({"kind": "demo"}, "connected GitHub"), ({"number": None}, "connected GitHub")])
def test_publishing_refuses_when_a_precondition_is_missing(tmp_path, monkeypatch, kwargs, fragment):
    called = []
    out, _ = publish(tmp_path, monkeypatch, fn=lambda *a, **k: called.append(1), **kwargs)
    assert out["published"] is False and fragment in out["reason"] and not called


def test_a_successful_publish_is_recorded_and_the_token_stays_out_of_the_log(tmp_path, monkeypatch):
    out, log = publish(tmp_path, monkeypatch, fn=lambda report, cfg, repo, token, head_sha, **kw: {"check_run": 11, "comment": 22})
    assert out == {"published": True, "check_run": 11, "comment": 22}
    assert TOKEN not in json.dumps(log.since(0))


def test_github_refusing_the_check_run_is_reported_not_hidden(tmp_path, monkeypatch):
    out, log = publish(tmp_path, monkeypatch, fn=lambda *a, **k: {"check_run_error": 403, "comment": 22})
    assert out["published"] is True and out["check_run_error"] == 403
    assert "GitHub rejected" in log.since(0)[-1]["message"]
    out, log = publish(tmp_path, monkeypatch, fn=lambda *a, **k: {"check_run_error": 403, "comment_error": 403})
    assert out["published"] is False and log.since(0)[-1]["kind"] == "stage_failed"


def test_publish_exceptions_are_contained_and_redacted(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError(f"401 for token {TOKEN}")

    out, log = publish(tmp_path, monkeypatch, fn=boom)
    assert out["published"] is False and TOKEN not in json.dumps(out) + json.dumps(log.since(0))


# --- .env loading and the token check ---------------------------------------------------------------------------------


def test_env_file_parsing_and_precedence(tmp_path, monkeypatch):
    from sentinel.envfile import load_env, parse

    assert parse('A=1\n# c\n\nexport B="two words"\nC=\'x\'\nD=\nE=val # note\nF=a#b\nbad line\n') == {"A": "1", "B": "two words", "C": "x", "E": "val", "F": "a#b"}
    path = tmp_path / ".env"
    path.write_text("SENTINEL_T1=from-file\nSENTINEL_T2=from-file\nSENTINEL_T3=\n", encoding="utf-8")
    monkeypatch.setenv("SENTINEL_T2", "from-environment")
    monkeypatch.delenv("SENTINEL_T1", raising=False)
    loaded = load_env(path)
    import os

    assert loaded == ["SENTINEL_T1"] and os.environ["SENTINEL_T1"] == "from-file"
    assert os.environ["SENTINEL_T2"] == "from-environment"  # a real environment variable wins
    assert "SENTINEL_T3" not in os.environ  # blank values are ignored
    monkeypatch.delenv("SENTINEL_T1", raising=False)
    assert load_env(tmp_path / "missing.env") == []


def test_token_check_reports_access_without_returning_the_token(monkeypatch):
    fine = "github_pat_" + "B2c3D4e5" * 6
    monkeypatch.setenv("GITHUB_TOKEN", fine)

    def handler(request):
        path = request.url.path
        if path == "/rate_limit":
            return httpx.Response(200, json={"resources": {"core": {"limit": 5000, "remaining": 4990}}})
        if path == "/user":
            return httpx.Response(403)  # fine-grained tokens often cannot read /user
        if path == "/repos/o/private-r":
            return httpx.Response(200, json={"full_name": "o/private-r", "private": True, "permissions": {"pull": True, "push": False, "admin": False}})
        return httpx.Response(404)

    result = client(handler).access("o/private-r")
    assert result["valid"] is True and result["token_kind"] == "fine-grained personal access token" and result["rate_limit"]["limit"] == 5000
    assert result["repository"]["permissions"]["pull"] is True and result["problems"] == [] and result["login"] is None
    assert fine not in json.dumps(result)
    hidden = client(handler).access("o/not-granted")
    assert hidden["repository"]["accessible"] is False and any("cannot see" in p for p in hidden["problems"])


def test_token_check_explains_a_bad_or_missing_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("SENTINEL_GITHUB_TOKEN", raising=False)
    assert client(lambda r: httpx.Response(500)).access()["configured"] is False
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "Z9y8X7w6" * 5)
    bad = client(lambda r: httpx.Response(401)).access()
    assert bad["valid"] is False and "revoked or expired" in bad["problems"][0]
    ignored = client(lambda r: httpx.Response(200, json={"resources": {"core": {"limit": 60, "remaining": 59}}})).access()
    assert any("unauthenticated" in p for p in ignored["problems"])
