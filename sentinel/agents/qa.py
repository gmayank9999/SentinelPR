"""Q&A bot: answers questions about the repository with citations, or refuses.

Modes (the RQ4 conditions)
    ``no_rag``        the model answers from its own knowledge (baseline; LLM only)
    ``code``          retrieval over code chunks only
    ``code_history``  retrieval over code and history (commits, issues, PR discussions)

Every sentence of an answer must cite a retrieved id; uncited sentences are dropped by the
citation guard. When nothing relevant is retrieved the bot says it cannot find the answer.
Without an LLM the bot answers extractively: it quotes the best-supported sentences from the
retrieved history and points to the code.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from langchain_core.prompts import ChatPromptTemplate

from sentinel.agents.langchain_adapter import SentinelRetriever
from sentinel.agents.prompts import load_prompt
from sentinel.guards.input import find_injections
from sentinel.guards.output import enforce_citations
from sentinel.retrieval.text import tokenize

REFUSAL = "I can't find that in this repository."
INJECTION_REFUSAL = "I can only answer questions about this repository's code and history."
MIN_SUPPORT = 0.34  # share of question terms that must appear in the best context
LOCATION_QUESTION = re.compile(r"^\s*(where|which (code|modules?|functions?)|what (calls|uses|depends))\b", re.IGNORECASE)
SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Answer:
    question: str
    answer: str
    citations: list[str]
    refused: bool
    mode: str
    contexts: list[dict] = field(default_factory=list)
    source: str = "extractive"
    latency_s: float = 0.0
    tokens: int = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _citations(text: str) -> list[str]:
    return sorted({c.strip() for group in re.findall(r"\[([^\]]+)\]", text) for c in group.split(",")})


def _best_sentence(text: str, terms: set[str]) -> str | None:
    """The most relevant declarative sentence. In issue/PR threads the replies (lines after the
    opening post) usually carry the explanation, so they get a small boost."""
    lines = text.split("\n")[1:]
    candidates = []
    for index, line in enumerate(lines):
        if line.startswith(("files:", "functions:")):
            continue  # commit metadata, not prose
        for sentence in SENTENCE.split(line):
            sentence = sentence.strip()
            if len(sentence) < 20 or sentence.endswith("?"):
                continue
            score = _support(terms, sentence) + (0.25 if index >= 1 and ": " in sentence[:40] else 0.0)
            candidates.append((score, sentence))
    if not candidates:
        return None
    score, sentence = max(candidates, key=lambda c: c[0])
    return sentence if score > 0 else None


def _support(question_terms: set[str], text: str) -> float:
    if not question_terms:
        return 0.0
    return len(question_terms & set(tokenize(text))) / len(question_terms)


class QABot:
    def __init__(self, retriever, llm=None, top_k: int = 6):
        self.retriever = retriever
        self.llm = llm
        self.top_k = top_k

    def retrieve(self, question: str, mode: str) -> list[dict]:
        if mode == "no_rag":
            return []
        collections = ("code",) if mode == "code" else ("history", "code")
        docs = SentinelRetriever(retriever=self.retriever, collections=collections, top_k=self.top_k).invoke(question)
        return [{"id": d.metadata["id"], "text": d.page_content, "score": d.metadata["score"], "kind": d.metadata["kind"],
                 "title": d.metadata.get("title")} for d in docs][: self.top_k * 2]

    def answer(self, question: str, mode: str = "code_history", use_llm: bool = True) -> Answer:
        started = time.time()
        if find_injections(question, "question"):
            return Answer(question, INJECTION_REFUSAL, [], True, mode, source="guard", latency_s=round(time.time() - started, 3))
        contexts = self.retrieve(question, mode)
        terms = {t for t in tokenize(question) if len(t) > 2} - {"why", "what", "where", "when", "how", "does", "code", "function"}
        grounded = [c for c in contexts if _support(terms, c["text"]) >= MIN_SUPPORT]

        if use_llm and self.llm is not None and self.llm.available:
            result = self._llm_answer(question, mode, contexts)
            if result is not None:
                result.latency_s = round(time.time() - started, 3)
                return result
        # A specific number in the question (a year, a count) that no evidence mentions means the
        # repository cannot answer it, however related the rest of the wording looks.
        numbers = set(re.findall(r"\b\d{2,}\b", question))
        if numbers and not any(n in c["text"] for n in numbers for c in grounded):
            grounded = []
        if not grounded:
            return Answer(question, REFUSAL, [], True, mode, contexts, latency_s=round(time.time() - started, 3))
        return self._extractive(question, mode, grounded, terms, started)

    def _extractive(self, question: str, mode: str, grounded: list[dict], terms: set[str], started: float) -> Answer:
        history = [c for c in grounded if c["kind"] != "code"]
        # application code before tests; "where"/"what calls" questions are about code first
        code = sorted((c for c in grounded if c["kind"] == "code"), key=lambda c: "chunk:tests/" in c["id"])
        if LOCATION_QUESTION.match(question):
            places = [f"{c['text'].splitlines()[0].replace('# file: ', '')} [{c['id']}]" for c in code[:3]]
            if places:
                text = "See " + "; ".join(places) + "."
                return Answer(question, text, _citations(text), False, mode, grounded, "extractive", round(time.time() - started, 3))
        sentences = []
        for doc in history[:3]:
            best = _best_sentence(doc["text"], terms)
            if best:
                sentences.append(f"{best} [{doc['id']}]")
        if code:
            head = code[0]
            sentences.append(f"The relevant code is {head['text'].splitlines()[0].replace('# file: ', '')} [{head['id']}].")
        text = " ".join(dict.fromkeys(sentences)) or REFUSAL
        citations = _citations(text)
        return Answer(question, text, citations, text == REFUSAL, mode, grounded, "extractive", round(time.time() - started, 3))

    def _llm_answer(self, question: str, mode: str, contexts: list[dict]) -> Answer | None:
        prompt = load_prompt("qa")
        if mode == "no_rag":
            system, user = prompt.system.replace("using ONLY the context provided", "using what you know"), question
        else:
            template = ChatPromptTemplate.from_messages([("system", prompt.system), ("human", prompt.user.replace("$question", "{question}").replace("$context", "{context}"))])
            context = "\n\n".join(f"[{c['id']}]\n{c['text'][:1200]}" for c in contexts) or "(nothing retrieved)"
            messages = template.invoke({"question": question, "context": context}).to_messages()
            system, user = messages[0].content, messages[1].content
        try:
            text, call = self.llm.complete("qa", system, user, max_tokens=500)
        except Exception:
            return None
        tokens = call.prompt_tokens + call.completion_tokens
        if REFUSAL.lower().rstrip(".") in text.lower():
            return Answer(question, REFUSAL, [], True, mode, contexts, "llm", tokens=tokens)
        if mode == "no_rag":
            return Answer(question, text.strip(), [], False, mode, [], "llm", tokens=tokens)
        guarded = enforce_citations(text, {c["id"] for c in contexts})
        if guarded.kept == 0:
            return Answer(question, REFUSAL, [], True, mode, contexts, "llm", tokens=tokens)
        return Answer(question, guarded.text, _citations(guarded.text), False, mode, contexts, "llm", tokens=tokens)
