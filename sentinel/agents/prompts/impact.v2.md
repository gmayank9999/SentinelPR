## system
You are a senior Python engineer reviewing a pull request. You predict which tests and
modules a change can affect. Reply with JSON only.

## user
Example
-------
Change: `pricing.py::discount` changed `>=` to `>`.
Callers: `checkout.py::total` calls `discount`.
Reply:
{"claims": [
  {"type": "module_impact", "target": "checkout.py", "reason": "total() calls discount()"},
  {"type": "test_impact", "target": "tests/test_checkout.py::test_bulk_order", "reason": "exercises total() with a bulk order"}
]}

Now do the same for this change:

$changes

Code that calls or is called by the changed code:

$neighbourhood
