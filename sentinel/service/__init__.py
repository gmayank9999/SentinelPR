"""The product service layer: repositories, analysis runs, live execution events.

It wraps the existing pipeline (``sentinel.run.analyse``) without changing what it computes:
a run manager queues analyses, every stage reports through ``sentinel.orchestrator.observe``,
and the events are persisted per run so a finished run can be replayed exactly.
"""
