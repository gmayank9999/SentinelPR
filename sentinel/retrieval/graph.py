"""Code knowledge graph.

Nodes
    ``file:<path>``           a source file
    ``sym:<path>::<qualname>`` a function, method or class (test functions included)
    ``commit:<sha>``, ``issue:<n>``, ``pr:<n>``

Edges (attribute ``type``)
    contains        file -> sym, class -> method
    imports         file -> file
    calls           sym -> sym     (``confidence`` < 1 when the callee name was ambiguous)
    inherits        class -> class
    tests           test sym -> sym  (from the per-test coverage map: the test executes it)
    modified_in     sym -> commit
    fixes           commit -> issue
    introduced_bug  commit -> sym  (SZZ)
    discussed_in    sym -> pr
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Iterable
from pathlib import Path

import networkx as nx

from sentinel.retrieval.chunker import ModuleInfo, parse_python

AMBIGUITY_LIMIT = 3
STRUCTURAL = ("calls", "inherits")


def sym_id(path: str, qualname: str) -> str:
    return f"sym:{path}::{qualname}"


def unit_to_node(unit_id: str) -> str:
    """Change-unit id ("path::qualname") -> graph node id."""
    return "sym:" + unit_id


def node_to_unit(node: str) -> str:
    return node[4:] if node.startswith("sym:") else node


def is_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith("tests/") or "/tests/" in path or name.startswith("test_")


def module_to_path(module: str, files: set[str], current: str | None = None) -> str | None:
    """Resolve a (possibly relative) module name to a project file."""
    if module.startswith("."):
        if current is None:
            return None
        level = len(module) - len(module.lstrip("."))
        base = current.split("/")[:-level]
        rest = module.lstrip(".")
        parts = base + (rest.split(".") if rest else [])
    else:
        parts = module.split(".")
    for candidate in ("/".join(parts) + ".py", "/".join(parts) + "/__init__.py"):
        if candidate in files:
            return candidate
    return None


class CodeGraph:
    def __init__(self, graph: nx.MultiDiGraph | None = None):
        self.g = graph if graph is not None else nx.MultiDiGraph()
        self._by_name: dict[str, list[str]] | None = None

    # construction -----------------------------------------------------------
    @classmethod
    def build(cls, sources: dict[str, str], store=None, test_ids: Iterable[str] = ()) -> "CodeGraph":
        graph = cls()
        infos = {path: parse_python(text, path) for path, text in sources.items() if path.endswith(".py")}
        graph._add_structure(infos)
        if store is not None:
            graph._add_coverage(store, infos)
            graph._add_history(store)
        graph._attach_test_ids(test_ids)
        return graph

    def _add_structure(self, infos: dict[str, ModuleInfo]) -> None:
        g = self.g
        files = set(infos)
        for path, info in infos.items():
            g.add_node(f"file:{path}", kind="file", path=path, is_test=is_test_path(path))
            for s in info.symbols:
                node = sym_id(path, s.qualname)
                g.add_node(
                    node,
                    kind=s.kind,
                    path=path,
                    qualname=s.qualname,
                    name=s.name,
                    start=s.start,
                    end=s.end,
                    signature=s.signature,
                    is_test=is_test_path(path) and (s.name.startswith("test") or s.kind == "class" and s.name.startswith("Test")),
                )
                parent = sym_id(path, s.parent) if s.parent else f"file:{path}"
                g.add_edge(parent, node, type="contains")

        by_name: dict[str, list[str]] = defaultdict(list)
        for path, info in infos.items():
            for s in info.symbols:
                by_name[s.name].append(sym_id(path, s.qualname))
        self._by_name = by_name

        for path, info in infos.items():
            imported: dict[str, str] = {}  # bound name -> node or file
            for ref in info.imports:
                target_file = module_to_path(ref.module, files, path)
                if ref.name and ref.name != "*":
                    # "from pkg import module" binds a module, "from module import name" a symbol
                    sub_module = module_to_path(f"{ref.module}.{ref.name}" if not ref.module.endswith(".") else ref.module + ref.name, files, path)
                    if sub_module:
                        g.add_edge(f"file:{path}", f"file:{sub_module}", type="imports")
                        continue
                    if target_file:
                        g.add_edge(f"file:{path}", f"file:{target_file}", type="imports")
                        target = sym_id(target_file, ref.name)
                        if target in g:
                            imported[ref.bound_name] = target
                elif target_file:
                    g.add_edge(f"file:{path}", f"file:{target_file}", type="imports")

            local = {s.name: sym_id(path, s.qualname) for s in info.symbols if s.parent is None}
            for s in info.symbols:
                source = sym_id(path, s.qualname)
                for callee, confidence in self._resolve_calls(s.calls, imported, local, by_name, s.parent and sym_id(path, s.parent)):
                    if callee != source:
                        g.add_edge(source, callee, type="calls", confidence=confidence)
                for base in s.bases:
                    base_name = base.split(".")[-1]
                    for target, confidence in self._resolve_calls([base_name], imported, local, by_name, None):
                        if g.nodes[target].get("kind") == "class":
                            g.add_edge(source, target, type="inherits", confidence=confidence)

    def _resolve_calls(self, names, imported, local, by_name, owner_class) -> list[tuple[str, float]]:
        resolved: dict[str, float] = {}
        for name in names:
            if name in imported:
                resolved[imported[name]] = 1.0
                continue
            if name in local:
                resolved[local[name]] = 1.0
                continue
            candidates = by_name.get(name, [])
            if owner_class:
                sibling = f"{owner_class}.{name}"
                if sibling in candidates:
                    resolved[sibling] = 1.0
                    continue
            candidates = [c for c in candidates if not self.g.nodes[c].get("is_test")]
            if 0 < len(candidates) <= AMBIGUITY_LIMIT:
                for c in candidates:
                    resolved[c] = max(resolved.get(c, 0.0), 1.0 / len(candidates))
        return list(resolved.items())

    def _add_coverage(self, store, infos: dict[str, ModuleInfo]) -> None:
        """tests edges: test function -> every non-test symbol whose lines it executes."""
        added: set[tuple[str, str]] = set()
        for path, info in infos.items():
            if is_test_path(path):
                continue
            lines = [n for s in info.symbols for n in range(s.def_line, s.end + 1)]
            if not lines:
                continue
            for test_id, covered in store.tests_covering(path, lines).items():
                test_node = self.test_node_for(test_id)
                if test_node is None:
                    continue
                for s in info.symbols:
                    target = sym_id(path, s.qualname)
                    if (test_node, target) not in added and any(s.def_line <= n <= s.end for n in covered):
                        added.add((test_node, target))
                        self.g.add_edge(test_node, target, type="tests")

    def _add_history(self, store) -> None:
        g = self.g
        for commit in store.commits():
            c_node = f"commit:{commit['sha']}"
            g.add_node(c_node, kind="commit", sha=commit["sha"], message=commit["message"].splitlines()[0] if commit["message"] else "",
                       author=commit["author"], timestamp=commit["timestamp"], is_fix=commit["is_fix"])
            for unit in commit["functions"]:
                node = unit_to_node(unit)
                if node in g:
                    g.add_edge(node, c_node, type="modified_in")
            if commit["is_fix"]:
                for number in commit["issues"]:
                    g.add_node(f"issue:{number}", kind="issue", number=number)
                    g.add_edge(c_node, f"issue:{number}", type="fixes")
            pr = store.pr_for_commit(commit["sha"])
            if pr:
                p_node = f"pr:{pr['number']}"
                g.add_node(p_node, kind="pr", number=pr["number"], title=pr["title"])
                for unit in commit["functions"]:
                    node = unit_to_node(unit)
                    if node in g:
                        g.add_edge(node, p_node, type="discussed_in")
        for issue in store.issues():
            node = f"issue:{issue['number']}"
            g.add_node(node, kind="issue", number=issue["number"], title=issue["title"], labels=issue["labels"])
        for link in store.szz_links():
            if not link.get("function"):
                continue
            target = sym_id(link["file"], link["function"])
            if target in g:
                g.add_edge(f"commit:{link['introducing_sha']}", target, type="introduced_bug",
                           fix=link["fix_sha"], issue=link.get("issue"), confidence=link.get("confidence", 1.0))

    def _attach_test_ids(self, test_ids: Iterable[str]) -> None:
        for test_id in test_ids:
            node = self.test_node_for(test_id)
            if node is not None:
                self.g.nodes[node].setdefault("test_ids", [])
                if test_id not in self.g.nodes[node]["test_ids"]:
                    self.g.nodes[node]["test_ids"].append(test_id)

    def test_node_for(self, test_id: str) -> str | None:
        path, _, rest = test_id.partition("::")
        qualname = rest.split("[", 1)[0].replace("::", ".")
        node = sym_id(path, qualname)
        return node if node in self.g else None

    # queries ----------------------------------------------------------------
    def edges_of_type(self, node: str, edge_type: str, reverse: bool = False) -> list[tuple[str, dict]]:
        if node not in self.g:
            return []
        if reverse:
            return [(u, d) for u, _, d in self.g.in_edges(node, data=True) if d.get("type") == edge_type]
        return [(v, d) for _, v, d in self.g.out_edges(node, data=True) if d.get("type") == edge_type]

    def callers(self, node: str, depth: int = 3) -> dict[str, int]:
        """Transitive callers (and subclasses) of ``node`` -> distance."""
        return self._bfs(node, depth, reverse=True, types=STRUCTURAL)

    def callees(self, node: str, depth: int = 1) -> dict[str, int]:
        return self._bfs(node, depth, reverse=False, types=STRUCTURAL)

    def _bfs(self, start: str, depth: int, reverse: bool, types: tuple[str, ...]) -> dict[str, int]:
        seen = {start: 0}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            if seen[node] >= depth:
                continue
            for other, data in (self._in(node) if reverse else self._out(node)):
                if data.get("type") in types and other not in seen:
                    seen[other] = seen[node] + 1
                    queue.append(other)
        seen.pop(start)
        return seen

    def _in(self, node):
        return ((u, d) for u, _, d in self.g.in_edges(node, data=True)) if node in self.g else ()

    def _out(self, node):
        return ((v, d) for _, v, d in self.g.out_edges(node, data=True)) if node in self.g else ()

    def neighbourhood(self, seeds: Iterable[str], hops: int = 2) -> dict[str, int]:
        """Symbols within ``hops`` structural edges of any seed, in either direction."""
        distances: dict[str, int] = {}
        for seed in seeds:
            if seed not in self.g:
                continue
            distances[seed] = 0
            for node, d in self._bfs(seed, hops, reverse=True, types=STRUCTURAL).items():
                distances[node] = min(d, distances.get(node, d))
            for node, d in self._bfs(seed, hops, reverse=False, types=STRUCTURAL).items():
                distances[node] = min(d, distances.get(node, d))
        return distances

    def static_tests_for(self, node: str, depth: int = 5) -> dict[str, int]:
        """Test functions that can reach ``node`` through calls (static reachability)."""
        return {n: d for n, d in self.callers(node, depth).items() if self.g.nodes[n].get("is_test")}

    def covering_tests(self, node: str) -> list[str]:
        return [u for u, _ in self.edges_of_type(node, "tests", reverse=True)]

    def call_path(self, source: str, target: str, depth: int = 5) -> list[str] | None:
        """Shortest call chain source -> ... -> target."""
        view = nx.DiGraph((u, v) for u, v, d in self.g.edges(data=True) if d.get("type") in STRUCTURAL)
        try:
            path = nx.shortest_path(view, source, target)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None
        return path if len(path) - 1 <= depth else None

    def bug_history(self, node: str) -> list[dict]:
        return [
            {"introducing_sha": u.split(":", 1)[1], **d}
            for u, d in self.edges_of_type(node, "introduced_bug", reverse=True)
        ]

    def commits_for(self, node: str) -> list[str]:
        return [v for v, _ in self.edges_of_type(node, "modified_in")]

    def symbols_named(self, name: str) -> list[str]:
        if self._by_name is None:
            self._by_name = defaultdict(list)
            for node, data in self.g.nodes(data=True):
                if node.startswith("sym:"):
                    self._by_name[data.get("name", "")].append(node)
        return list(self._by_name.get(name, []))

    def node(self, node: str) -> dict:
        return dict(self.g.nodes[node]) if node in self.g else {}

    def test_ids_for(self, node: str) -> list[str]:
        return list(self.g.nodes[node].get("test_ids", [])) if node in self.g else []

    @staticmethod
    def evidence(edge_type: str, source: str, target: str) -> str:
        def short(n: str) -> str:
            return n.split("::")[-1] if n.startswith("sym:") else n
        return f"graph:{edge_type}:{short(source)}->{short(target)}"

    # persistence --------------------------------------------------------------
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(nx.node_link_data(self.g, edges="edges")), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "CodeGraph":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(nx.node_link_graph(data, directed=True, multigraph=True, edges="edges"))

    def stats(self) -> dict:
        kinds: dict[str, int] = defaultdict(int)
        for _, data in self.g.nodes(data=True):
            kinds[data.get("kind", "?")] += 1
        edges: dict[str, int] = defaultdict(int)
        for _, _, data in self.g.edges(data=True):
            edges[data.get("type", "?")] += 1
        return {"nodes": dict(kinds), "edges": dict(edges)}
