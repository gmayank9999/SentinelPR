"""SQLite storage for everything SentinelPR learns about a repository and every run it makes."""

from __future__ import annotations

import json
import sqlite3
import time
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS commits (
    sha TEXT PRIMARY KEY,
    parents TEXT,
    message TEXT,
    author TEXT,
    email TEXT,
    timestamp INTEGER,
    files TEXT,            -- JSON [{path, additions, deletions, status}]
    functions TEXT,        -- JSON ["path::qualname"]
    additions INTEGER,
    deletions INTEGER,
    is_fix INTEGER DEFAULT 0,
    issues TEXT            -- JSON [issue numbers referenced]
);
CREATE INDEX IF NOT EXISTS commits_ts ON commits(timestamp);
CREATE TABLE IF NOT EXISTS commit_files (
    sha TEXT,
    file TEXT,
    additions INTEGER,
    deletions INTEGER,
    author TEXT,
    timestamp INTEGER
);
CREATE INDEX IF NOT EXISTS commit_files_file ON commit_files(file);
CREATE TABLE IF NOT EXISTS commit_functions (
    sha TEXT,
    unit TEXT,             -- "path::qualname"
    timestamp INTEGER
);
CREATE INDEX IF NOT EXISTS commit_functions_unit ON commit_functions(unit);
CREATE TABLE IF NOT EXISTS issues (
    number INTEGER PRIMARY KEY,
    title TEXT,
    body TEXT,
    labels TEXT,
    state TEXT,
    author TEXT,
    comments TEXT,
    linked_prs TEXT,
    created_at TEXT,
    closed_at TEXT
);
CREATE TABLE IF NOT EXISTS prs (
    number INTEGER PRIMARY KEY,
    title TEXT,
    body TEXT,
    author TEXT,
    reviews TEXT,
    linked_issues TEXT,
    merge_sha TEXT,
    merged_at TEXT
);
CREATE TABLE IF NOT EXISTS szz_links (
    fix_sha TEXT,
    introducing_sha TEXT,
    file TEXT,
    function TEXT,
    line INTEGER,
    confidence REAL,
    issue INTEGER
);
CREATE INDEX IF NOT EXISTS szz_function ON szz_links(file, function);
CREATE TABLE IF NOT EXISTS coverage (
    file TEXT,
    line INTEGER,
    test_id TEXT
);
CREATE INDEX IF NOT EXISTS coverage_file_line ON coverage(file, line);
CREATE INDEX IF NOT EXISTS coverage_test ON coverage(test_id);
CREATE TABLE IF NOT EXISTS tests (
    test_id TEXT PRIMARY KEY,
    file TEXT,
    duration REAL,
    outcome TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    pr INTEGER,
    head_sha TEXT,
    base_sha TEXT,
    decision TEXT,
    risk REAL,
    tokens INTEGER,
    latency REAL,
    created_at REAL,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT,
    run_id TEXT,
    agent TEXT,
    type TEXT,
    target TEXT,
    assertion TEXT,
    reason TEXT,
    evidence_ids TEXT
);
CREATE TABLE IF NOT EXISTS verdicts (
    claim_id TEXT,
    run_id TEXT,
    status TEXT,
    method TEXT,
    evidence TEXT,
    detail TEXT
);
"""


def _j(value: Any) -> str:
    return json.dumps(value, default=str)


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # meta -----------------------------------------------------------------
    def set_meta(self, key: str, value: Any) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, _j(value)))
        self.conn.commit()

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    # commits --------------------------------------------------------------
    def replace_commits(self, commits: Iterable[dict]) -> int:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM commits")
        cur.execute("DELETE FROM commit_files")
        cur.execute("DELETE FROM commit_functions")
        count = 0
        for c in commits:
            count += 1
            cur.execute(
                "INSERT INTO commits VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    c["sha"], _j(c["parents"]), c["message"], c["author"], c["email"], c["timestamp"],
                    _j(c["files"]), _j(c["functions"]), c["additions"], c["deletions"],
                    int(c.get("is_fix", False)), _j(c.get("issues", [])),
                ),
            )
            for f in c["files"]:
                cur.execute(
                    "INSERT INTO commit_files VALUES (?,?,?,?,?,?)",
                    (c["sha"], f["path"], f["additions"], f["deletions"], c["author"], c["timestamp"]),
                )
            for unit in c["functions"]:
                cur.execute("INSERT INTO commit_functions VALUES (?,?,?)", (c["sha"], unit, c["timestamp"]))
        self.conn.commit()
        return count

    def _commit_row(self, row: sqlite3.Row) -> dict:
        record = dict(row)
        for key in ("parents", "files", "functions", "issues"):
            record[key] = json.loads(record[key] or "[]")
        record["is_fix"] = bool(record["is_fix"])
        return record

    def commit(self, sha: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM commits WHERE sha = ? OR sha LIKE ?", (sha, f"{sha}%")).fetchone()
        return self._commit_row(row) if row else None

    def commits(self, limit: int | None = None) -> list[dict]:
        query = "SELECT * FROM commits ORDER BY timestamp"
        if limit:
            query += f" LIMIT {int(limit)}"
        return [self._commit_row(r) for r in self.conn.execute(query)]

    def commits_touching_unit(self, unit: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT c.* FROM commits c JOIN commit_functions f ON c.sha = f.sha WHERE f.unit = ? ORDER BY c.timestamp",
            (unit,),
        )
        return [self._commit_row(r) for r in rows]

    def commits_touching_file(self, path: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT DISTINCT c.* FROM commits c JOIN commit_files f ON c.sha = f.sha WHERE f.file = ? ORDER BY c.timestamp",
            (path,),
        )
        return [self._commit_row(r) for r in rows]

    def file_history(self, path: str) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM commit_files WHERE file = ? ORDER BY timestamp", (path,)))

    # tracker --------------------------------------------------------------
    def replace_tracker(self, issues: Iterable[dict], pulls: Iterable[dict]) -> tuple[int, int]:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM issues")
        cur.execute("DELETE FROM prs")
        n_issues = n_prs = 0
        for i in issues:
            n_issues += 1
            cur.execute(
                "INSERT OR REPLACE INTO issues VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    i["number"], i.get("title", ""), i.get("body") or "", _j(i.get("labels", [])), i.get("state", "open"),
                    i.get("author", ""), _j(i.get("comments", [])), _j(i.get("linked_prs", [])),
                    i.get("created_at"), i.get("closed_at"),
                ),
            )
        for p in pulls:
            n_prs += 1
            cur.execute(
                "INSERT OR REPLACE INTO prs VALUES (?,?,?,?,?,?,?,?)",
                (
                    p["number"], p.get("title", ""), p.get("body") or "", p.get("author", ""), _j(p.get("reviews", [])),
                    _j(p.get("linked_issues", [])), p.get("merge_commit_sha"), p.get("merged_at"),
                ),
            )
        self.conn.commit()
        return n_issues, n_prs

    def _issue_row(self, row: sqlite3.Row) -> dict:
        record = dict(row)
        for key in ("labels", "comments", "linked_prs"):
            record[key] = json.loads(record[key] or "[]")
        return record

    def issue(self, number: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM issues WHERE number = ?", (number,)).fetchone()
        return self._issue_row(row) if row else None

    def issues(self) -> list[dict]:
        return [self._issue_row(r) for r in self.conn.execute("SELECT * FROM issues ORDER BY number")]

    def _pr_row(self, row: sqlite3.Row) -> dict:
        record = dict(row)
        for key in ("reviews", "linked_issues"):
            record[key] = json.loads(record[key] or "[]")
        return record

    def pr(self, number: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM prs WHERE number = ?", (number,)).fetchone()
        return self._pr_row(row) if row else None

    def prs(self) -> list[dict]:
        return [self._pr_row(r) for r in self.conn.execute("SELECT * FROM prs ORDER BY number")]

    def pr_for_commit(self, sha: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM prs WHERE merge_sha = ?", (sha,)).fetchone()
        return self._pr_row(row) if row else None

    # SZZ ------------------------------------------------------------------
    def replace_szz(self, links: Iterable[dict]) -> int:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM szz_links")
        rows = [
            (l["fix_sha"], l["introducing_sha"], l["file"], l.get("function"), l.get("line"), l.get("confidence", 1.0), l.get("issue"))
            for l in links
        ]
        cur.executemany("INSERT INTO szz_links VALUES (?,?,?,?,?,?,?)", rows)
        self.conn.commit()
        return len(rows)

    def szz_links(self, file: str | None = None, function: str | None = None) -> list[dict]:
        query, args = "SELECT * FROM szz_links WHERE 1=1", []
        if file:
            query += " AND file = ?"
            args.append(file)
        if function:
            query += " AND function = ?"
            args.append(function)
        return [dict(r) for r in self.conn.execute(query, args)]

    # coverage -------------------------------------------------------------
    def replace_coverage(self, rows: Iterable[tuple[str, int, str]], tests: Iterable[dict]) -> int:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM coverage")
        cur.execute("DELETE FROM tests")
        rows = list(rows)
        cur.executemany("INSERT INTO coverage VALUES (?,?,?)", rows)
        cur.executemany(
            "INSERT OR REPLACE INTO tests VALUES (?,?,?,?)",
            [(t["test_id"], t["file"], t.get("duration", 0.0), t.get("outcome", "passed")) for t in tests],
        )
        self.conn.commit()
        return len(rows)

    def tests_covering(self, file: str, lines: Iterable[int]) -> dict[str, set[int]]:
        """test_id -> the subset of ``lines`` it executes."""
        lines = list(lines)
        result: dict[str, set[int]] = defaultdict(set)
        for start in range(0, len(lines), 500):
            batch = lines[start : start + 500]
            placeholders = ",".join("?" * len(batch))
            for row in self.conn.execute(
                f"SELECT line, test_id FROM coverage WHERE file = ? AND line IN ({placeholders})", [file, *batch]
            ):
                result[row["test_id"]].add(row["line"])
        return dict(result)

    def covered_lines(self, file: str) -> set[int]:
        return {r["line"] for r in self.conn.execute("SELECT DISTINCT line FROM coverage WHERE file = ?", (file,))}

    def lines_of_test(self, test_id: str) -> dict[str, set[int]]:
        result: dict[str, set[int]] = defaultdict(set)
        for row in self.conn.execute("SELECT file, line FROM coverage WHERE test_id = ?", (test_id,)):
            result[row["file"]].add(row["line"])
        return dict(result)

    def all_tests(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM tests ORDER BY test_id")]

    def has_coverage(self) -> bool:
        return self.conn.execute("SELECT 1 FROM coverage LIMIT 1").fetchone() is not None

    # runs -----------------------------------------------------------------
    def save_run(self, run: dict) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                run["run_id"], run.get("pr"), run.get("head_sha"), run.get("base_sha"), run.get("decision"),
                run.get("risk"), run.get("tokens", 0), run.get("latency", 0.0), time.time(), _j(run),
            ),
        )
        cur.execute("DELETE FROM claims WHERE run_id = ?", (run["run_id"],))
        cur.execute("DELETE FROM verdicts WHERE run_id = ?", (run["run_id"],))
        for claim in run.get("claims", []):
            cur.execute(
                "INSERT INTO claims VALUES (?,?,?,?,?,?,?,?)",
                (
                    claim["claim_id"], run["run_id"], claim["agent"], claim["type"], claim["target"],
                    claim.get("assertion", ""), claim.get("reason", ""), _j(claim.get("evidence_ids", [])),
                ),
            )
        for verdict in run.get("verdicts", []):
            cur.execute(
                "INSERT INTO verdicts VALUES (?,?,?,?,?,?)",
                (verdict["claim_id"], run["run_id"], verdict["status"], verdict["method"], _j(verdict.get("evidence", [])), verdict.get("detail", "")),
            )
        self.conn.commit()

    def runs(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute("SELECT payload FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))
        return [json.loads(r["payload"]) for r in rows]
