# Code search: Sourcegraph, Zoekt and the local fallback

Hybrid retrieval fuses four candidate generators with reciprocal rank fusion:

1. **Graph** — callers and callees of the changed symbols (k hops in the code knowledge graph)
2. **Code search** — exact usages of the changed symbols' names
3. **BM25** — keyword search over code chunks
4. **Dense** — embedding similarity (hashing embedder by default; sentence-transformers or Ollama
   embeddings when configured; Chroma or FAISS as vector stores)

`retrieval.mode` selects a subset (`lexical`, `dense`, `graph`, `hybrid`) for ablations.

## Backends for exact search

`sentinel/retrieval/lexical.py` implements one interface over three engines. Every query is
recorded in the run report (`retrieval.queries`) and cited as `search:<backend>:<query>`.

| backend | when | configuration |
|---|---|---|
| **Zoekt** | a local trigram index, fast on large repositories | `retrieval.zoekt_url: http://localhost:6070` (`docker compose --profile search up zoekt`) |
| **Sourcegraph** | the course lab instance, or sourcegraph.com for public repositories | `retrieval.sourcegraph_url`, `retrieval.sourcegraph_repo`, optional `SRC_ACCESS_TOKEN` |
| **Local scan** | always available; the default | nothing |

Definitions and bare import lines are excluded from "usages", so a search for
`calculateStudentCredits` returns the places that call it.

## Sourcegraph queries used during exploration

Useful queries against the demo repository (replace the repo path with your fork):

```
repo:^github\.com/gmayank9999/SentinelPR$ calculateStudentCredits\( file:^demo/uni-erp/unierp
repo:^github\.com/gmayank9999/SentinelPR$ type:symbol compute_gpa
repo:^github\.com/gmayank9999/SentinelPR$ "if :[a] >= :[b]:" patternType:structural file:\.py$
repo:^github\.com/gmayank9999/SentinelPR$ Decimal\("0\.\d+"\) patternType:regexp
```

The first finds every call site of the central credit function (the same set the Impact agent's
call graph reports); the structural query lists boundary comparisons, which is where most of the
benchmark's hidden faults live.

## How it feeds the Impact agent

For each changed symbol the agent issues a usage search for its name; each hit is mapped to the
code chunk that contains it and becomes one ranked list in the fusion. Search is therefore
evidence the model can cite, not text it has to trust.
