from __future__ import annotations

"""Compatibility wrapper for legacy imports.

Use app.instagram_safe as the canonical implementation.
"""

from .instagram_safe import (  # noqa: F401
    DEFAULT_RATE_CONFIG,
    assess_login_readiness,
    collect_profile_streaming,
    evaluate_login_state,
    prepare_login_session,
)

