"""MOP — Model Output Protocol.

Evaluates agent output against active rules and rewrites violations.
"""

from .filter import (
    Action,
    AcceptedVerdict,
    MopConfig,
    RejectedVerdict,
    RewrittenVerdict,
    Verdict,
    evaluate,
)
from .rewrite import TelegramMessage, rewrite

__all__ = [
    "Action",
    "AcceptedVerdict",
    "MopConfig",
    "RejectedVerdict",
    "RewrittenVerdict",
    "TelegramMessage",
    "Verdict",
    "evaluate",
    "rewrite",
]
