# SPDX-License-Identifier: AGPL-3.0-or-later
"""Filtering a wall of things (people, Sites, tags, collections, photo sets, songs) by its own rows.

A repeated key is OR within a facet and different keys AND across them. Named parameters rather than
`q=`, because the query language's words are the file's. Safe as the file filter is: fixed `narrow`
templates, every value bound, one conjunct under fixed permission rules.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sift.kernel.access.filter_parts import AGE_YEARS, HEIGHT_BAND, ConstraintError
from sift.kernel.access.sites import SITE_CONCEALED


@dataclass(frozen=True, slots=True)
class EntityFacet:
    """One dimension of a wall of things, declared once so a row counts the set it selects.

    `value` is grouped by and sent back as the filter. `narrow` binds one array at `{}`, separately,
    because a multi-valued dimension cannot be read off the row. `label` names a value that is an
    ID.
    """

    value: str
    narrow: str
    joins: str = ""
    label: str | None = None

    def __post_init__(self) -> None:
        if self.narrow.count("{}") != 1:
            raise ConstraintError("a narrowing binds exactly one array of values")


# `noqa: S608` below: a column or table named at the call site is spliced, never a value.
def _word(column: str) -> EntityFacet:
    """A stash-box word, folded so `BLONDE` from a box and `Blonde` typed in are one row."""
    return EntityFacet(
        value=f"UPPER({column})",
        narrow=f"UPPER({column}) IN (SELECT UPPER(value) FROM json_each({{}}))",  # noqa: S608
    )


def _number(column: str) -> EntityFacet:
    """A year, compared as text because an address carries every value as a word."""
    return EntityFacet(
        value=f"CAST({column} AS TEXT)",
        narrow=f"CAST({column} AS TEXT) IN (SELECT value FROM json_each({{}}))",  # noqa: S608
    )


def _band(expression: str) -> EntityFacet:
    """A number in bands; the band is both the group and the match."""
    return EntityFacet(
        value=expression,
        narrow=f"({expression}) IN (SELECT value FROM json_each({{}}))",  # noqa: S608
    )


def _age(expression: str) -> EntityFacet:
    """An age, picked as one (`27`) or as a span an older address carries (`25-29`, `50+`)."""
    age = f"CAST({expression} AS INTEGER)"
    return EntityFacet(
        value=expression,
        narrow=(
            "EXISTS (SELECT 1 FROM json_each({}) nv WHERE"  # noqa: S608
            f" ({expression}) = nv.value"
            " OR (nv.value GLOB '[0-9]*-[0-9]*' AND " + age + " BETWEEN"
            " CAST(substr(nv.value, 1, instr(nv.value, '-') - 1) AS INTEGER)"
            " AND CAST(substr(nv.value, instr(nv.value, '-') + 1) AS INTEGER))"
            " OR (nv.value GLOB '[0-9]*+' AND " + age + " >="
            " CAST(rtrim(nv.value, '+') AS INTEGER)))"
        ),
    )


def _presence(condition: str) -> EntityFacet:
    """A yes-or-no dimension as `yes` and `no`, so either or both can be picked like any facet."""
    value = f"CASE WHEN {condition} THEN 'yes' ELSE 'no' END"
    return EntityFacet(value=value, narrow=value + " IN (SELECT value FROM json_each({}))")  # noqa: S608


def _tag_shown(tag: str, held: str) -> str:
    """Whether this viewer is shown the tag `tag`; `held` names the lookup's alias."""
    return (
        f"(:reveal_named = 1 OR NOT EXISTS (SELECT 1 FROM tag_user_state {held}"  # noqa: S608
        f" WHERE {held}.tag_id = {tag} AND {held}.user_id = :viewer AND {held}.hidden = 1))"
    )


def _own_tags(table: str, key: str, alias: str) -> EntityFacet:
    """The tags on the thing itself by ID, led by `any` and `none`. A tag hidden from this viewer
    neither counts nor makes the thing tagged: a name with a count is what concealment withholds."""

    def tagged(link: str) -> str:
        return (
            f"EXISTS (SELECT 1 FROM {table} {link} WHERE {link}.{key} = {alias}.id"  # noqa: S608
            f" AND {_tag_shown(link + '.tag_id', link + 'h')})"
        )

    return EntityFacet(
        # Each thing twice: once per tag, and once more as `any` or `none`.
        value=f"CASE WHEN fxk.k = 1 THEN fxt.id WHEN {tagged('fxa')} THEN 'any' ELSE 'none' END",
        label="fxt.name",
        joins=(
            "\n  JOIN (SELECT 1 AS k UNION ALL SELECT 0) fxk"
            f"\n  LEFT JOIN {table} fxjt ON fxk.k = 1 AND fxjt.{key} = {alias}.id"
            "\n  LEFT JOIN tags fxt ON fxt.id = fxjt.tag_id AND " + _tag_shown("fxt.id", "fxth")
        ),
        narrow=(
            "EXISTS (SELECT 1 FROM json_each({}) nv WHERE"  # noqa: S608
            f" (nv.value = 'any' AND {tagged('nta')})"
            f" OR (nv.value = 'none' AND NOT {tagged('ntn')})"
            f" OR EXISTS (SELECT 1 FROM {table} nt WHERE nt.{key} = {alias}.id"
            " AND nt.tag_id = nv.value))"
        ),
    )


def _linked(table: str, key: str, alias: str) -> EntityFacet:
    """Whether a stash-box is attached; its link tables exist on every install."""
    return _presence(f"EXISTS (SELECT 1 FROM {table} fxl WHERE fxl.{key} = {alias}.id)")  # noqa: S608


#: The bound name of the disagreeing ids, one spelling for the count and the filter.
DISAGREEING = "disagreeing"

#: The one facet whose values are NOT in the database. See `_disagrees`.
DISAGREES = "disagrees"


def _disagrees(alias: str) -> EntityFacet:
    """Whether a linked stash-box disagrees with this record.

    A list of ids bound in, because the rule is applied in Python by the slice owning the record
    (`DisagreementSeam.subjects_with_disagreements`), and a SQL copy could drift. Worked out only
    when `asks_disagreements` says so.
    """
    return _presence(f"{alias}.id IN (SELECT value FROM json_each(:{DISAGREEING}))")  # noqa: S608


def _enriched_by_box(table: str, key: str, alias: str) -> EntityFacet:
    """Which boxes have written to this row, or `none`.

    `linked` stays declared so an old address still filters. `none` is a value here, because the
    rows no box knows are worth a list; a box with no slug reads NULL and is left out.
    """
    return EntityFacet(
        value=f"CASE WHEN fxe.{key} IS NULL THEN 'none' ELSE fxeb.slug END",
        joins=(
            f"\n  LEFT JOIN {table} fxe ON fxe.{key} = {alias}.id"
            "\n  LEFT JOIN stash_boxes fxeb ON fxeb.id = fxe.box_id"
        ),
        # From the picked values outwards, so the array is named once.
        narrow=(
            "EXISTS (SELECT 1 FROM json_each({}) nv WHERE"  # noqa: S608
            f" (nv.value = 'none' AND NOT EXISTS"
            f" (SELECT 1 FROM {table} nx WHERE nx.{key} = {alias}.id))"
            f" OR EXISTS (SELECT 1 FROM {table} ne"
            f" JOIN stash_boxes nb ON nb.id = ne.box_id"
            f" WHERE ne.{key} = {alias}.id AND nb.slug = nv.value))"
        ),
    )


def _created_by(alias: str, *, boxes: bool = True) -> EntityFacet:
    """Who made this row: a stash-box by slug, Sift by the task that made it (`sift` before tasks
    were recorded, and still every row Sift made), or the user asking (`me`). Another user's rows
    read NULL and drop out: naming them would publish who made what."""
    by_box = " ELSE fxcb.slug END" if boxes else " END"
    box_join = f"\n  LEFT JOIN stash_boxes fxcb ON fxcb.id = {alias}.created_by_box_id"
    box_match = (
        f" OR EXISTS (SELECT 1 FROM stash_boxes ncb WHERE ncb.id = {alias}.created_by_box_id"  # noqa: S608
        " AND ncb.slug = nv.value)"
    )
    return EntityFacet(
        # A Stash library's unattached person, Site or tag keeps its own row
        # (`vocabulary.VIA_STASH_UNATTACHED`), which the via split gives it whoever made it.
        value=(
            f"CASE WHEN {alias}.created_by_via = 'stash_unattached' THEN 'stash_unattached'"
            f" WHEN {alias}.created_by_kind = 'sift' THEN COALESCE({alias}.created_by_via, 'sift')"
            f" WHEN {alias}.created_by_user_id = :viewer THEN 'me'" + by_box
        ),
        joins=box_join if boxes else "",
        narrow=(
            "EXISTS (SELECT 1 FROM json_each({}) nv WHERE"  # noqa: S608
            f" (nv.value = 'stash_unattached' AND {alias}.created_by_via = 'stash_unattached')"
            f" OR (nv.value = 'sift' AND {alias}.created_by_kind = 'sift'"
            f" AND {alias}.created_by_via IS NOT 'stash_unattached')"
            f" OR ({alias}.created_by_kind = 'sift' AND {alias}.created_by_via = nv.value)"
            f" OR (nv.value = 'me' AND {alias}.created_by_user_id = :viewer)"
            + (box_match if boxes else "")
            + ")"
        ),
    )


def _cover(alias: str) -> EntityFacet:
    """Whether the row has a picture this viewer would be shown, by the wall's own cover verdict."""
    return _presence(
        f"{alias}.cover_upload_id IS NOT NULL"  # noqa: S608
        " OR EXISTS (SELECT 1 FROM viewer_assets fxcv"
        f" WHERE fxcv.asset_id = {alias}.cover_asset_id"
        " AND fxcv.user_id = :viewer AND (:reveal_named = 1 OR fxcv.concealed = 0))"
    )


def _sharing(object_type: str, alias: str) -> EntityFacet:
    """What has been shared and held back. Admin only, because it describes other users' decisions;
    a thing can be under both."""
    decided = "CASE fxg.effect WHEN 'share' THEN 'shared' ELSE 'restricted' END"
    return EntityFacet(
        value=decided,
        joins=(
            f"\n  JOIN acl_grants fxg ON fxg.object_type = '{object_type}'"
            f" AND fxg.object_id = {alias}.id AND fxg.effect IN ('share', 'restrict')"
        ),
        narrow=(
            f"EXISTS (SELECT 1 FROM acl_grants ng WHERE ng.object_type = '{object_type}'"  # noqa: S608
            f" AND ng.object_id = {alias}.id"
            " AND (CASE ng.effect WHEN 'share' THEN 'shared' ELSE 'restricted' END)"
            " IN (SELECT value FROM json_each({})))"
        ),
    )


#: Every facet of every wall of things, by noun; `test_facets_match_the_registry.py` holds it to the
#: registry. Each is written in its wall's alias in `repository/entities.py`.
ENTITY_FACETS: dict[str, dict[str, EntityFacet]] = {
    "person": {
        "gender": _word("p.gender"),
        "hair_color": _word("p.hair_color"),
        "eye_color": _word("p.eye_color"),
        "ethnicity": _word("p.ethnicity"),
        # The ISO code; the client names the country in the reader's language.
        "country": _word("p.country"),
        "breast_type": _word("p.breast_type"),
        "height_cm": _band(HEIGHT_BAND.format(col="p.height_cm")),
        "age": _age(AGE_YEARS.format(col="p.birth_date")),
        "career_start_year": _number("p.career_start_year"),
        # The column refuses NULL, so the two rows add up to the wall.
        "pmv_creator": _presence("p.pmv_creator = 1"),
        "tags": _own_tags("person_tags", "person_id", "p"),
        "linked": _linked("person_stash_box_links", "person_id", "p"),
        "enriched": _enriched_by_box("person_stash_box_links", "person_id", "p"),
        "created": _created_by("p"),
        DISAGREES: _disagrees("p"),
        "cover": _cover("p"),
        "sharing": _sharing("person", "p"),
    },
    "site": {
        # No `kind` facet: nothing fills it. `parent` is the network, by id with its name.
        "parent": EntityFacet(
            value="fxp.id",
            label="fxp.name",
            joins=(
                # A network concealed anywhere up its chain is no value (`sites.py`).
                "\n  JOIN sites fxp ON fxp.id = pl.parent_id"
                " AND (:reveal_named = 1 OR NOT " + SITE_CONCEALED.format(site="fxp.id") + ")"
            ),
            narrow="pl.parent_id IN (SELECT value FROM json_each({}))",
        ),
        "tags": _own_tags("site_tags", "site_id", "pl"),
        "linked": _linked("site_stash_box_links", "site_id", "pl"),
        "enriched": _enriched_by_box("site_stash_box_links", "site_id", "pl"),
        "created": _created_by("pl"),
        DISAGREES: _disagrees("pl"),
        "usernames": _presence("EXISTS (SELECT 1 FROM usernames fxac WHERE fxac.site_id = pl.id)"),
        "cover": _cover("pl"),
        "sharing": _sharing("site", "pl"),
    },
    "tag": {
        # The tag this one is filed under; a hidden parent is no value.
        "parent": EntityFacet(
            value="fxp.id",
            label="fxp.name",
            joins=(
                "\n  JOIN tags fxp ON fxp.id = t.parent_id"
                " AND (:reveal_named = 1 OR NOT EXISTS (SELECT 1 FROM tag_user_state fxph"
                " WHERE fxph.tag_id = fxp.id AND fxph.user_id = :viewer AND fxph.hidden = 1))"
            ),
            narrow="t.parent_id IN (SELECT value FROM json_each({}))",
        ),
        "category": _word("t.category"),
        "linked": _linked("tag_stash_box_links", "tag_id", "t"),
        "enriched": _enriched_by_box("tag_stash_box_links", "tag_id", "t"),
        "created": _created_by("t"),
        DISAGREES: _disagrees("t"),
        "cover": _cover("t"),
        "sharing": _sharing("tag", "t"),
    },
    "collection": {
        "mine": _presence("c.owner_id = :viewer"),
        "tags": _own_tags("collection_tags", "collection_id", "c"),
        "cover": _cover("c"),
        "created": _created_by("c", boxes=False),
        "sharing": _sharing("collection", "c"),
    },
    "photo_set": {
        "tags": _own_tags("photo_set_tags", "photo_set_id", "ps"),
        "cover": _cover("ps"),
        "created": _created_by("ps", boxes=False),
        "sharing": _sharing("photo_set", "ps"),
    },
    # A song's artists by id, which a filter keeps when a name is corrected.
    "song": {
        "artists": EntityFacet(
            value="fxa.id",
            label="fxa.name",
            joins=(
                "\n  JOIN song_artists fxsa ON fxsa.song_id = sg.id"
                "\n  JOIN artists fxa ON fxa.id = fxsa.artist_id"
            ),
            narrow=(
                "EXISTS (SELECT 1 FROM song_artists na WHERE na.song_id = sg.id"
                " AND na.artist_id IN (SELECT value FROM json_each({})))"
            ),
        ),
        "cover": _cover("sg"),
        "created": _created_by("sg", boxes=False),
        "sharing": _sharing("song", "sg"),
    },
}


#: Admin-only facets, as `ADMIN_FACETS` on the wall of files; settling what a box wrote is an
#: admin's act.
ADMIN_ENTITY_FACETS = frozenset({"sharing", DISAGREES})


@dataclass(frozen=True, slots=True)
class EntityNarrowing:
    """What a wall of things was filtered to. `impossible` matches nothing: an admin-only key asked
    by somebody else must not widen."""

    subject: str
    picks: tuple[tuple[str, tuple[str, ...]], ...] = ()
    #: The values a wall was told "not this" about, per facet: a row matching any of them is out.
    refused: tuple[tuple[str, tuple[str, ...]], ...] = ()
    impossible: bool = False
    #: None is "nobody asked", unlike the empty tuple (see `of`).
    disagreeing: tuple[str, ...] | None = None

    @classmethod
    def of(
        cls,
        subject: str,
        picks: Mapping[str, Sequence[str] | None],
        *,
        is_admin: bool = False,
        disagreeing: Sequence[str] | None = None,
    ) -> EntityNarrowing:
        """A filter from a route's parameters, in the facets' declared order so one question is one
        statement. A `disagrees` pick without `disagreeing` is refused; a leading `-` refuses a
        value."""
        facets = ENTITY_FACETS.get(subject)
        if facets is None:
            raise ConstraintError(f"{subject!r} is not a wall of things")
        chosen: list[tuple[str, tuple[str, ...]]] = []
        refused: list[tuple[str, tuple[str, ...]]] = []
        impossible = False
        for key in facets:
            values = picks.get(key)
            if not values:
                continue
            if key in ADMIN_ENTITY_FACETS and not is_admin:
                impossible = True
                continue
            if key == DISAGREES and disagreeing is None:
                raise ConstraintError("a disagreement pick needs the records that disagree")
            read = [read_pick(one) for one in values]
            wanted = [name for refused, name in read if not refused]
            unwanted = [name for refused, name in read if refused]
            if wanted:
                chosen.append((key, tuple(sorted(dict.fromkeys(wanted)))))
            if unwanted:
                refused.append((key, tuple(sorted(dict.fromkeys(unwanted)))))
        for key in picks:
            if key not in facets:
                raise ConstraintError(f"{key!r} is not a facet of {subject}")
        return cls(
            subject=subject,
            picks=tuple(chosen),
            refused=tuple(refused),
            impossible=impossible,
            disagreeing=None if disagreeing is None else tuple(disagreeing),
        )

    def predicate(self) -> tuple[str, dict[str, object]]:
        """One SQL conjunct and its values: `1` for no filter, `0` for one that cannot match."""
        if self.impossible:
            return "0", {}
        # Always bound: the facet count reuses this pair, and counting `disagrees` needs it.
        bound: dict[str, object] = {DISAGREEING: json.dumps(list(self.disagreeing or ()))}
        parts: list[str] = []
        for index, (key, values) in enumerate(self.picks):
            name = f"e{index}"
            bound[name] = json.dumps(list(values))
            parts.append("(" + ENTITY_FACETS[self.subject][key].narrow.format(f":{name}") + ")")
        for index, (key, values) in enumerate(self.refused):
            name = f"x{index}"
            bound[name] = json.dumps(list(values))
            # COALESCE: a row with no value is not a refused value, and NOT NULL would drop it.
            matched = ENTITY_FACETS[self.subject][key].narrow.format(f":{name}")
            parts.append(f"NOT COALESCE(({matched}), 0)")
        if not parts:
            return "1", bound
        return " AND ".join(parts), bound


def is_refusal(value: str) -> bool:
    """Whether a facet value says "not this" (a leading minus; a lone `-` is a value)."""
    return value.startswith("-") and len(value) > 1


def read_pick(value: str) -> tuple[bool, str]:
    """A facet value as (refused, name). Quotes hold a name as written: `"-raw"` is a name."""
    refused = is_refusal(value)
    name = value[1:] if refused else value
    if len(name) > 1 and name.startswith('"') and name.endswith('"'):
        name = name[1:-1]
    return refused, name


def asks_disagreements(picks: Mapping[str, Sequence[str] | None], counting: str = "") -> bool:
    """Whether the disagreeing ids are needed: a wall filtered on them, or the panel counting them.
    Nothing else pays for one enrichment plan per link."""
    return bool(picks.get(DISAGREES)) or counting == DISAGREES


#: The empty narrowing, one object everywhere.
NO_NARROWING = EntityNarrowing(subject="")
