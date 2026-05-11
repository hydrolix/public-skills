"""Canonical delta helpers for the report engine.

Thin wrapper around ``scripts.baselines`` that exposes a single import path
context modules can use instead of re-implementing the
``(current - baseline) / max(baseline, 1.0) * 100`` formula and reading
``baselines`` directly. Adds ``signed_delta_pp`` for percentage-point
deltas between two share/rate values that arrive already in percent units.

Note: the two ``contexts/`` modules that currently divide by ``baseline``
directly (with a manual zero-guard returning ``0.0``) have intentionally
*different* semantics from :func:`pct_delta` here — they return ``0.0`` on
zero baseline while ``pct_delta`` clamps the denominator to ``1.0``. Those
sites are out of scope for M1.1; revisiting them is a separate task.
"""

from __future__ import annotations

import baselines as _baselines


def pct_delta(current: float, baseline: float) -> float:
    """Percent change from ``baseline`` to ``current``.

    The denominator is ``max(baseline, 1.0)``, so a baseline of zero yields
    ``current * 100`` rather than a division error. Inherited from the
    legacy renderer's behavior.
    """
    return _baselines.pct_delta(current, baseline)


def direction(delta: float) -> str:
    """Return ``"increase"`` / ``"decrease"`` / ``"no_change"`` for ``delta``."""
    return _baselines.direction(delta)


def signed_delta_pp(current_pct: float, baseline_pct: float) -> float:
    """Percentage-point delta between two values already expressed as percentages.

    Use when both inputs are share/rate values in ``[0, 100]``. For
    instance, ``signed_delta_pp(42.5, 40.0)`` returns ``2.5`` (a
    +2.5 pp change), not 6.25 (the +6.25% relative change that
    :func:`pct_delta` would return).
    """
    return float(current_pct) - float(baseline_pct)
