"""MOP output-gate plugin for Hermes.

This directory lives in the model-output-protocol repo and is symlinked into
``~/.hermes/plugins/mop``. MOP owns its own host interface; Hermes just loads
it. ``register(ctx)`` wires the ``transform_llm_output`` hook to MOP's gate.

Default behaviour is **log mode**: every outgoing message is evaluated and
audited, but delivery is never altered (the callback returns ``None``, so
Hermes leaves the text unchanged). Shift to enforcement deliberately via
``MOP_MODE=enforce`` once rules have been activated and vetted — see plugin.yaml
for the env surface.

Robustness: the plugin resolves the MOP package off its own real path (so the
symlink works without installing MOP into Hermes' venv), and the callback is
fail-open — any error logs and passes the message through unchanged. It can
never withhold a message by malfunctioning.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# The plugin dir is <mop-repo>/integrations/hermes/. resolve() follows the
# ~/.hermes/plugins/mop symlink back to the real path in the repo, so parents[2]
# is the repo root where the importable `mop` package lives.
_MOP_REPO = Path(__file__).resolve().parents[2]
if str(_MOP_REPO) not in sys.path:
    sys.path.insert(0, str(_MOP_REPO))


def register(ctx) -> None:
    """Hermes plugin entrypoint. Wire MOP's gate to transform_llm_output."""
    from mop import gate_from_env  # imported here so a bad import fails just this plugin

    def transform_llm_output(*, response_text: str = "", **kwargs) -> str | None:
        """Sync hook: evaluate + audit; return a replacement or None (passthrough)."""
        if not response_text:
            return None
        try:
            result = gate_from_env(response_text, host="hermes")
        except Exception as exc:  # fail-open: never withhold on our own bug
            logger.warning("MOP gate failed, passing message through: %s", exc)
            return None
        return result.replacement()

    ctx.register_hook("transform_llm_output", transform_llm_output)
    logger.info("MOP plugin registered (mode=%s)", os.environ.get("MOP_MODE", "log"))
