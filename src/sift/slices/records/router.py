# SPDX-License-Identifier: AGPL-3.0-or-later
"""The field registry, served so no screen holds its own copy of a field to drift."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from sift.kernel.access import Viewer
from sift.kernel.records import every_field
from sift.slices.auth import current_viewer
from sift.slices.records.models import FieldDescription, FieldRegistry

router = APIRouter(tags=["records"])


@router.get("/records/fields")
async def read_fields(
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> FieldRegistry:
    """Every field, grouped by subject, in the registry's reading order; the same for everybody."""
    grouped: dict[str, list[FieldDescription]] = {}
    for one in every_field():
        grouped.setdefault(one.subject.value, []).append(
            FieldDescription(
                key=one.key,
                subject=one.subject.value,
                label=one.label,
                kind=one.kind.value,
                shown=one.shown.value,
                group=one.group.value,
                editable=one.editable,
                imported=one.imported,
                help=one.help,
                suggests=one.suggests,
                links_to=one.links_to,
                entry=one.entry,
                ordered=one.ordered,
            )
        )
    return FieldRegistry(subjects=grouped)
