# renewal-discount

Renewal invoices ignore loyalty discounts when a subscription crosses its annual renewal boundary. Find the discount policy responsible and fix it.

Run `python -m pytest -q tests/test_renewal_discount.py` to reproduce. Fix the code if a test fails; otherwise report the responsible file and function.

This is the prompt for task `renewal-discount`. `traceweaver ab --task renewal-discount` drives the same task through Claude Code hooks without calling the `claude` binary. A live `claude -p` run happens only when that CLI is logged in.
