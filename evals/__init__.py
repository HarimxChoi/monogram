"""Monogram evaluation harness.

Cassette-replay architecture for testing the 5-stage pipeline without
burning LLM quota on every test run. See MONOGRAM_EVAL_PLAN.md for the
architecture doc.

THIS PACKAGE IS A PURE CONSUMER of src/monogram/. Nothing in
src/monogram/ may import from evals/ (CI-enforced).
"""
__version__ = "0.7.0-dev"
