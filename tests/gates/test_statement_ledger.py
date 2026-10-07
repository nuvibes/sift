# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every statement a screen or a press runs costs no more than its record, in SQLite's steps.

The synthetic library is built at two sizes and the application booted over each; every screen's
reads and every press are asked as the admin and as a guest (`sift.testing.statement_ledger`).
Steps are SQLite's own count of its work, so the same tree on the same SQLite gives the same
numbers on any machine and under any load. The record is changed on purpose, with
`scripts/statement_ledger.py`, and only ever falls on its own.
"""

from __future__ import annotations

from typing import Any

import pytest

from sift.testing import statement_ledger as ledger

pytestmark = pytest.mark.gate


@pytest.fixture(scope="module")
def record() -> dict[str, Any]:
    recorded = ledger.read_record().get(ledger.version())
    if recorded is None:
        pytest.skip(
            f"the statement ledger has no record for SQLite {ledger.version()}: steps differ "
            "between versions, so write one on this version with "
            "`python scripts/statement_ledger.py --start` (the suite's ledger-record job, "
            "run by hand) and commit it"
        )
    return dict(recorded)


#: Each size walked once per run, and only when a case asks for it.
_WALKED: dict[int, ledger.Walk] = {}


def _walk(size: int, factory: pytest.TempPathFactory) -> ledger.Walk:
    if size not in _WALKED:
        _WALKED[size] = ledger.walk(size, directory=factory.mktemp(f"ledger{size}"))
    return _WALKED[size]


def _walked(size: int, factory: pytest.TempPathFactory) -> dict[str, ledger.Seen]:
    return _walk(size, factory).seen


@pytest.fixture(scope="module")
def walks(tmp_path_factory: pytest.TempPathFactory) -> dict[int, dict[str, ledger.Seen]]:
    return {size: _walked(size, tmp_path_factory) for size in ledger.SIZES}


@pytest.fixture(scope="module")
def booted(tmp_path_factory: pytest.TempPathFactory) -> dict[int, dict[str, ledger.Seen]]:
    """The smaller walk alone: the boot's window is judged on it."""
    size = ledger.SIZES[0]
    return {size: _walked(size, tmp_path_factory)}


def test_a_guest_reads_by_id_a_file_they_are_shown(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Priced on the answer, not the refusal: a 404 reads nothing worth a record."""
    statuses = _walk(ledger.SIZES[0], tmp_path_factory).statuses
    by_id = [
        label
        for label in statuses
        if label.startswith("guest") and ("/api/assets/{id}" in label or "?asset=" in label)
    ]
    assert by_id
    assert {label: statuses[label] for label in by_id if statuses[label] != 200} == {}


def test_the_boot_walks_no_library_sized_table(
    record: dict[str, Any], booted: dict[int, dict[str, ledger.Seen]]
) -> None:
    """Before `boot.ready`, since the first screen waits for every statement there."""
    problems = ledger.boot_scans(record, booted)
    assert not problems, "\n".join(problems)


def test_no_statement_costs_more_than_its_record(
    record: dict[str, Any], walks: dict[int, dict[str, ledger.Seen]]
) -> None:
    """A rise past the noise is a regression or a decision, and a decision is recorded."""
    problems = ledger.rises(record, walks)
    assert not problems, "\n".join(problems)


def test_every_statement_is_in_the_record(
    record: dict[str, Any], walks: dict[int, dict[str, ledger.Seen]]
) -> None:
    """A new statement is priced and recorded on purpose, never discovered in a slow screen."""
    problems = ledger.unrecorded(record, walks)
    assert not problems, "\n".join(problems)


def test_no_request_walks_a_library_sized_table(
    record: dict[str, Any], walks: dict[int, dict[str, ledger.Seen]]
) -> None:
    """Off the sweep lane, unless the record already says so."""
    problems = ledger.scans(record, walks)
    assert not problems, "\n".join(problems)


def test_a_request_costs_the_same_on_a_library_four_times_the_size(
    record: dict[str, Any], walks: dict[int, dict[str, ledger.Seen]]
) -> None:
    """Unless the record names what it is priced by: a person's files, a folder's copies."""
    problems = ledger.growth(record, walks)
    assert not problems, "\n".join(problems)
