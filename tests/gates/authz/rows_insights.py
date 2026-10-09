# SPDX-License-Identifier: AGPL-3.0-or-later
"""Insights, and the browser client's own address."""

from __future__ import annotations

from tests.gates.authz.seeds import (
    A_RECAP,
    Case,
    Policy,
)

ROWS: dict[tuple[str, str], Case] = {
    ("GET", "/api/insights/path"): Case(Policy.AUTHENTICATED),
    # --- Insights: every read is narrowed to the asker inside the route.
    ("GET", "/api/insights"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/insights/visits"): Case(Policy.AUTHENTICATED),
    ("DELETE", "/api/insights/history"): Case(Policy.AUTHENTICATED),
    # --- Recaps: another User's recap answers the 404 an unminted id does; the sweep's users own none.
    ("GET", "/api/insights/recaps"): Case(Policy.AUTHENTICATED),
    ("GET", "/api/insights/recaps/{recap_id}"): Case(
        Policy.AUTHENTICATED, params={"recap_id": A_RECAP}, answers_anyway=404
    ),
    ("POST", "/api/insights/recaps/{recap_id}/dismiss"): Case(
        Policy.AUTHENTICATED, params={"recap_id": A_RECAP}, answers_anyway=404
    ),
    ("POST", "/api/insights/recaps/{recap_id}/video"): Case(
        Policy.AUTHENTICATED, params={"recap_id": A_RECAP}, answers_anyway=404
    ),
    ("GET", "/api/insights/recaps/{recap_id}/session"): Case(
        Policy.AUTHENTICATED, params={"recap_id": A_RECAP}, answers_anyway=404
    ),
    ("GET", "/api/insights/recaps/{recap_id}/keep"): Case(
        Policy.ADMIN, params={"recap_id": A_RECAP}, answers_anyway=404
    ),
    ("PUT", "/api/insights/recaps/{recap_id}/left-out"): Case(
        Policy.AUTHENTICATED, params={"recap_id": A_RECAP}, body={"ids": []}, answers_anyway=404
    ),
    # The browser client holds nothing; every endpoint it calls decides for itself.
    ("GET", "/{path:path}"): Case(Policy.PUBLIC, params={"path": ""}),
}
