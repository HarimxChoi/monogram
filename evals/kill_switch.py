"""Kill-switch precedence resolver for the eval system.

Three independent layers, checked in order. First disabling layer wins:

  Layer 1 (install-time): NOT installing `.[eval]` extras. Nothing in
    this module runs — evals/ is never imported. Implicit.

  Layer 2 (env var): MONOGRAM_EVAL_DISABLED=1
    Hardest switch. Overrides config. Use when config is unreachable
    (GitHub outage, PAT revoked, emergency).

  Layer 3 (vault config): eval_enabled: false in mono/config.md
    Normal user control. Persists.

  Layer 4 (Track B only, independent): classifier_few_shot_enabled
    Separate sub-switch; checked by classifier.py directly.

Every eval entrypoint — CLI command, cron harvest script, bot command
handler — calls is_eval_enabled() before doing work. On disabled,
print reason and exit 0 (not an error — user chose this).
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("monogram.evals.kill_switch")


def is_eval_enabled() -> tuple[bool, str]:
    """Check the three kill-switch layers.

    Returns (enabled, reason). When enabled is False, reason names the
    layer that disabled it — for logging and user-facing messages.
    """
    # Layer 2: env var (highest precedence)
    if os.environ.get("MONOGRAM_EVAL_DISABLED") == "1":
        return (False, "MONOGRAM_EVAL_DISABLED=1 in environment")

    # Layer 3: vault config
    # Imports lazily so tests can monkey-patch and so the module is
    # importable even when monogram config is partial.
    try:
        from monogram.vault_config import load_vault_config
        cfg = load_vault_config()
    except Exception as e:
        # If we can't read config, assume enabled — fail open to CI/tests.
        # The env var (layer 2) remains the reliable off-switch.
        log.info("kill_switch: vault_config unreadable, assuming enabled: %s", e)
        return (True, "")

    if cfg.eval_enabled is False:
        return (False, "eval_enabled: false in mono/config.md")

    return (True, "")


def require_enabled_or_exit(caller: str = "") -> None:
    """Call from eval CLI commands and cron scripts. Exits 0 on disabled."""
    import sys
    enabled, reason = is_eval_enabled()
    if not enabled:
        prefix = f"[{caller}] " if caller else ""
        print(f"{prefix}eval disabled: {reason}")
        print("    Enable via: /eval_enable (bot) or `monogram eval enable` (CLI)")
        sys.exit(0)


def is_few_shot_enabled() -> tuple[bool, str]:
    """Separate check for Track B classifier few-shot (Layer 4)."""
    try:
        from monogram.vault_config import load_vault_config
        cfg = load_vault_config()
    except Exception:
        return (False, "vault_config unreadable")
    if cfg.classifier_few_shot_enabled:
        return (True, "")
    return (False, "classifier_few_shot_enabled: false in mono/config.md")
