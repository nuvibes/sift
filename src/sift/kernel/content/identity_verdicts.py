# SPDX-License-Identifier: AGPL-3.0-or-later
"""What each pass concluded about a file, kept so the pass is not run again for nothing."""

from __future__ import annotations

import json
from collections.abc import Sequence

from sift.kernel.content.identity_models import Verdict, VerdictProduct, _verdict_from_row
from sift.kernel.content.identity_store import StoreCore
from sift.kernel.db import in_clause

# --- what a feature could not make for a file ------------------------------------------------
#
# See `_CREATE_FILE_VERDICTS`. One row per file and product; a second verdict for the same pair
# replaces the first, which is what a retry that fails again should do.
_RECORD_VERDICT = """
INSERT INTO file_verdicts (asset_id, product, code, reason, transient, at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(asset_id, product) DO UPDATE SET
  code = excluded.code, reason = excluded.reason, transient = excluded.transient, at = excluded.at
"""

_VERDICT_OF = "SELECT * FROM file_verdicts WHERE asset_id = ? AND product = ?"

_VERDICTS_OF = "SELECT * FROM file_verdicts WHERE asset_id = ? ORDER BY product"

_VERDICTED_AMONG = """
SELECT asset_id FROM file_verdicts WHERE asset_id IN (?*) AND product = ? AND transient = 0
"""

# The standing verdicts on a page of files, for the products a wall asked about. The products bind
# as one JSON array, so one statement answers "why was each of these left out" whatever they are.
_STANDING_AMONG = """
SELECT * FROM file_verdicts
 WHERE asset_id IN (?*) AND transient = 0 AND product IN (SELECT value FROM json_each(?))
 ORDER BY asset_id, product
"""

_COUNT_VERDICTS = "SELECT COUNT(*) AS total FROM file_verdicts WHERE product = ? AND transient = 0"

_VERDICTED_IDS = "SELECT asset_id FROM file_verdicts WHERE product = ? AND transient = 0"

_CLEAR_VERDICTS = "DELETE FROM file_verdicts WHERE product = ?"

_CLEAR_TRANSIENT_VERDICTS = "DELETE FROM file_verdicts WHERE asset_id = ? AND transient = 1"


class Verdicts(StoreCore):
    """What each pass concluded about a file."""

    # --- what a feature could not make for a file ------------------------------------------

    async def record_verdict(
        self,
        asset_id: str,
        product: VerdictProduct | str,
        *,
        code: str,
        reason: str,
        transient: bool = False,
    ) -> None:
        """Write down that a feature could not make this for this file, and why.

        `reason` is read on a screen and copied into a diagnostics export, so it carries no path
        and nothing read out of the bytes. `transient` says the verdict is about a moment (a
        share away, a file held open), and the next scan of the file clears it.
        """
        await self._write(
            _RECORD_VERDICT,
            (asset_id, str(product), code, reason, 1 if transient else 0, self._now()),
        )
        if str(product) == VerdictProduct.IDENTITY.value:
            self._legacy_remaining = None

    async def verdict_of(self, asset_id: str, product: VerdictProduct | str) -> Verdict | None:
        row = await self._db.fetch_one(_VERDICT_OF, (asset_id, str(product)))
        return None if row is None else _verdict_from_row(row)

    async def verdicts_of(self, asset_id: str) -> list[Verdict]:
        """Every verdict on one file, by product."""
        rows = await self._db.fetch_all(_VERDICTS_OF, (asset_id,))
        return [_verdict_from_row(row) for row in rows]

    async def verdicted_among(
        self, product: VerdictProduct | str, asset_ids: Sequence[str]
    ) -> set[str]:
        """Which of these files carry a standing verdict for this product. What a pass that
        decides its files a page at a time takes out of the page."""
        if not asset_ids:
            return set()
        sql, params = in_clause(_VERDICTED_AMONG, list(asset_ids))
        rows = await self._db.fetch_all(sql, [*params, str(product)])
        return {str(row["asset_id"]) for row in rows}

    async def standing_verdicts_among(
        self, asset_ids: Sequence[str], products: Sequence[str]
    ) -> dict[str, list[Verdict]]:
        """The standing verdicts on these files for these products, by file, products in order.

        What a wall filtered to the files a product gave up on (`left_out:`) reads to say, under
        each one, why. A page at a time, so the caller's page bounds it; a file with none is absent.
        """
        if not asset_ids or not products:
            return {}
        sql, params = in_clause(_STANDING_AMONG, list(asset_ids))
        rows = await self._db.fetch_all(sql, [*params, json.dumps(list(products))])
        found: dict[str, list[Verdict]] = {}
        for row in rows:
            verdict = _verdict_from_row(row)
            found.setdefault(verdict.asset_id, []).append(verdict)
        return found

    async def verdicted_ids(self, product: VerdictProduct | str) -> set[str]:
        """Every file this product has given up on. For a sweep that already holds its settled
        set in memory and takes these out of it the same way."""
        rows = await self._db.fetch_all(_VERDICTED_IDS, (str(product),))
        return {str(row["asset_id"]) for row in rows}

    async def verdict_count(self, product: VerdictProduct | str) -> int:
        """How many files this product has given up on. The sheet's own line for the row."""
        (row,) = await self._db.fetch_all(_COUNT_VERDICTS, (str(product),))
        return int(row["total"])

    async def clear_verdicts(self, product: VerdictProduct | str) -> int:
        """Forget every verdict for this product, so the next Build offers those files again.
        Returns how many."""
        async with self._db.write() as connection:
            cursor = await connection.execute(_CLEAR_VERDICTS, (str(product),))
            cleared = max(cursor.rowcount, 0)
        if str(product) == VerdictProduct.IDENTITY.value:
            self._legacy_remaining = None
        return cleared

    async def forget_transient_verdicts(self, asset_id: str) -> int:
        """A scan has seen the file again: what was true of a moment is not true of it now."""
        async with self._db.write() as connection:
            cursor = await connection.execute(_CLEAR_TRANSIENT_VERDICTS, (asset_id,))
            cleared = max(cursor.rowcount, 0)
        if cleared:
            self._legacy_remaining = None
        return cleared
