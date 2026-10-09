# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compiling a parsed query into the statement that answers it, the names in it resolved by id."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from sift.kernel.access import (
    MATCHES_NOTHING,
    MAX_PAGE_SIZE,
    SIMILARITY,
    AllOf,
    AnyOf,
    AssetFilter,
    ConstraintError,
    Not,
    Repository,
    Viewer,
    Where,
    fts_contains,
    fts_match,
)
from sift.kernel.access import (
    Node as Constraint,
)
from sift.kernel.content.user_state import resume_minimum_ms
from sift.kernel.ids import is_id
from sift.kernel.seams import SemanticSeam, SettingsSeam
from sift.slices.search.filter_facets import (
    CANDIDATE_CEILING,
    CANDIDATE_STEP,
    CANDIDATES,
    _cut_down,
    _folded,
    _ids_among,
    left_out_products,
    problems_in,
    scalar,
)
from sift.slices.search.filter_fields import (
    _DIMENSION,
    _PRESENCE,
    ENTITY_FIELDS,
    Field,
    Group,
    Negated,
    Node,
    Op,
    Presence,
    Problem,
    Query,
    Term,
)
from sift.slices.search.filter_parse import over_budget, parse


@dataclass(frozen=True, slots=True)
class Narrowed:
    """Constraints for a request, and whether they reach the end of what was looked through."""

    asset_filter: AssetFilter
    complete: bool = True
    #: The filters whose value nothing could act on, with the reason (see `problems_in`). Each
    #: matches nothing, and the page says so rather than reading as an empty library.
    problems: tuple[Problem, ...] = ()
    #: The products this query's `left_out:` terms ask about (see `left_out_products`), so a wall
    #: can say under each file why it was left out. Empty for every other query.
    left_out: tuple[str, ...] = ()


def _with_neighbours(
    described: AssetFilter, neighbours: tuple[tuple[str, float], ...]
) -> AssetFilter:
    """A query whose WORDS are answered two ways together: the index, or the model, or both."""
    named = Where("assets", tuple(asset_id for asset_id, _ in neighbours))
    words = AnyOf((named, AllOf(_text(described.text))))
    return replace(described, where=AllOf((described.where, words)), neighbours=neighbours)


class FilterCompiler:
    """Turns a query into constraints, for one particular viewer."""

    def __init__(
        self,
        access: Repository,
        *,
        semantic: SemanticSeam | None = None,
        settings: SettingsSeam | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._access = access
        # Reading two preferences, and only two: whether this user keeps a place in a video and
        # how long a video has to be before it does. `viewed:continue` is the one token whose answer
        # depends on them, and it depends on the ASKER's, so they are read per compile rather than
        # held, which is also what makes changing either take effect on the next screen.
        self._settings = settings
        # Asking a model what some words mean. Optional and handed in: the feature that owns the
        # index is a different slice, so this depends on the SHAPE and never on that slice, and an
        # install without it (or with it switched off) simply orders results the ordinary way.
        self._semantic = semantic
        self._clock = clock

    async def constrain(
        self, viewer: Viewer, raw: Mapping[str, str], *, match_text: bool = True
    ) -> AssetFilter:
        """The whole journey, for a request: raw parameters to bound constraints."""
        return await self.compile_query(viewer, parse(raw), match_text=match_text)

    async def narrow(
        self, viewer: Viewer, raw: Mapping[str, str], *, by_meaning: bool, need: int
    ) -> Narrowed:
        """The constraints for a request, including the ones a model was asked for."""
        # Parsed once here, for the problems and for the constraints alike. The reason a value
        # could not be read is kept beside the answer; the compiler still turns it into the
        # condition nothing satisfies, which is the honest result of asking for it.
        parsed = parse(raw)
        problems = tuple(problems_in(parsed, now=int(self._clock())))
        left_out = left_out_products(parsed)
        if not by_meaning:
            narrowed = await self._by_file(viewer, parsed, need=need)
        else:
            narrowed = await self._by_meaning(viewer, raw, need=need)
        return replace(narrowed, problems=problems, left_out=left_out)

    async def _widen(
        self,
        viewer: Viewer,
        ask: Callable[[int], Awaitable[tuple[AssetFilter, int] | None]],
        *,
        need: int,
    ) -> Narrowed | None:
        """Ask for a ranking, looking further down it until the page is full. None when the
        index declined to answer at all."""
        asked = CANDIDATES
        while True:
            answer = await ask(asked)
            if answer is None:
                return None
            narrowed, offered = answer
            reached = await self._reach(viewer, narrowed)
            # There is nothing further to look through: a list shorter than the ask is the index
            # saying it has run out, which makes this the whole answer however much was cut.
            if offered < asked:
                return Narrowed(narrowed)
            # Far enough. TWO conditions, and the second is the one easily left out: the page is
            # full AND the filtering left most of the candidates standing. A full page off a pool
            # that has been more than halved is a page of whatever survived, not of the closest
            # matches (see `_cut_down`).
            if reached >= need and not _cut_down(reached, offered):
                return Narrowed(narrowed)
            if asked >= CANDIDATE_CEILING:
                # `complete` is untouched by the rule above and says one thing: the page could not
                # be filled inside the ceiling. A FULL page off a cut pool is
                # still an answer somebody can read and page through: the screen's sentence for
                # `complete: false` tells them to filter the search, which would be the wrong
                # instruction and the wrong moment when they are looking at a full wall.
                return Narrowed(narrowed, complete=reached >= need)
            asked = min(asked * CANDIDATE_STEP, CANDIDATE_CEILING)

    async def _by_file(self, viewer: Viewer, query: Query, *, need: int) -> Narrowed:
        """A query, with every `like:` in it answered from far enough down its ranking."""
        if not any(isinstance(leaf, Term) and leaf.field is Field.LIKE for leaf in query.leaves()):
            return Narrowed(await self.compile_query(viewer, query))

        async def ask(reach: int) -> tuple[AssetFilter, int]:
            alike = await self._lookalikes(viewer, query, reach=reach)
            compiled = await self.compile_query(viewer, query, alike=alike)
            return compiled, max((len(pairs) for pairs in alike.values()), default=0)

        widened = await self._widen(viewer, ask, need=need)
        # `ask` always answers, so the loop always returns a page; the fallback only satisfies the
        # shape `_widen` shares with the words, whose index may decline.
        return widened or Narrowed(await self.compile_query(viewer, query))

    async def _by_meaning(self, viewer: Viewer, raw: Mapping[str, str], *, need: int) -> Narrowed:
        """The words answered by a model, looking further down the ranking until the page is full."""
        plain = await self.constrain(viewer, raw)
        semantic = self._semantic
        words = plain.text
        if semantic is None or not words:
            # Nothing to ask about the words, so the query is answered as any other is, including
            # the widening a `like:` in it gets.
            return await self._by_file(viewer, parse(raw), need=need)

        described = await self.constrain(viewer, raw, match_text=False)

        async def ask(reach: int) -> tuple[AssetFilter, int] | None:
            neighbours = await semantic.neighbours(words, limit=reach, asker=viewer)
            if neighbours is None:
                return None
            return _with_neighbours(described, neighbours), len(neighbours)

        widened = await self._widen(viewer, ask, need=need)
        return Narrowed(plain) if widened is None else widened

    async def _reach(self, viewer: Viewer, narrowed: AssetFilter) -> int:
        """How many rows this filter reaches, for this viewer. One row is fetched; the total is
        the answer, and it comes from the same statement the page will come from."""
        page = await self._access.visible_assets(
            viewer, limit=1, offset=0, asset_filter=narrowed, sort=SIMILARITY
        )
        return int(page.total)

    async def compile_query(
        self,
        viewer: Viewer,
        query: Query,
        *,
        match_text: bool = True,
        alike: Mapping[str, tuple[tuple[str, float], ...]] | None = None,
    ) -> AssetFilter:
        """A parsed query, resolved and turned into constraints."""
        if over_budget(query):
            return MATCHES_NOTHING

        found = await self._lookup(viewer, query)
        if alike is None:
            alike = await self._lookalikes(viewer, query, reach=CANDIDATES)
        found[Field.LIKE] = {
            value: tuple(asset_id for asset_id, _ in pairs) for value, pairs in alike.items()
        }
        folders: list[tuple[str, ...]] = []
        where = self._build(
            query.where, found, folders, now=int(self._clock()), admin=viewer.is_admin
        )
        # The History lines, already ids: nothing to look up, and nothing to resolve by name that a
        # viewer could learn from. Each is one more conjunct inside the same scoped read.
        where = _and(where, tuple(filing.where for filing in query.filings))

        try:
            return AssetFilter(
                where=_and(where, _text(query.text) if match_text else ()),
                text=query.text,
                folder_scope=tuple(folders),
                # How far each of those folders reaches. Carried on every filter rather than only
                # on one holding an `in:` term, for the reason `resume_min_ms` below is: the read
                # binds the parameter whether or not a folder was named.
                folder_depth=query.folder_depth,
                # Carried on every filter rather than only on one holding a `viewed:continue`,
                # because the `viewed` FACET reads the same parameter and a facet is counted from
                # the filter the screen is showing, which usually has no such term in it.
                resume_min_ms=await self._resume_minimum(viewer),
                # How near each file a `like:` named sits, so a wall of them can be closest first.
                # Replaced by the words' own ranking when the words are asked by meaning.
                neighbours=_closest(alike),
            )
        except ConstraintError:
            # Text that nothing could be matched by. Same answer as any other query that cannot
            # describe an asset.
            return MATCHES_NOTHING

    async def _resume_minimum(self, viewer: Viewer) -> int | None:
        """The shortest video this user keeps a place in, or None when they keep none."""
        if self._settings is None:
            return None
        return await resume_minimum_ms(self._settings.get_user, viewer.id)

    async def _lookalikes(
        self, viewer: Viewer, query: Query, *, reach: int
    ) -> dict[str, tuple[tuple[str, float], ...]]:
        """Each `like:` value's ranking: the files nearest it, closest first, `reach` deep."""
        wanted = sorted(
            {
                leaf.value
                for leaf in query.leaves()
                if isinstance(leaf, Term) and leaf.field is Field.LIKE and leaf.value.strip()
            }
        )
        found: dict[str, tuple[tuple[str, float], ...]] = {}
        for value in wanted:
            subject = value.strip()
            if (
                self._semantic is None
                or not is_id(subject)
                or not await self._access.can_view(viewer, subject)
            ):
                found[value] = ()
                continue
            found[value] = tuple(
                await self._semantic.lookalikes(subject, limit=reach, asker=viewer)
            )
        return found

    # --- resolving names ---------------------------------------------------------------------

    async def _lookup(
        self, viewer: Viewer, query: Query
    ) -> dict[Field, dict[str, tuple[str, ...]]]:
        """Every name in the query, resolved to ids, scoped to this viewer."""
        wanted: dict[Field, set[str]] = {}
        for leaf in query.leaves():
            # A token naming nothing is answered without asking the library, rather than by a full
            # suggester read filtered down to a name equal to the empty string: the right answer
            # by accident, paid for with a query per term.
            if isinstance(leaf, Term) and leaf.field in ENTITY_FIELDS and leaf.value.strip():
                wanted.setdefault(leaf.field, set()).add(leaf.value)

        return {
            field_name: await self.resolve(viewer, field_name, sorted(values))
            for field_name, values in wanted.items()
        }

    async def resolve(
        self, viewer: Viewer, field_name: Field, values: list[str]
    ) -> dict[str, tuple[str, ...]]:
        """What each value of one entity field names, for this viewer: ids, by id or by name."""
        if field_name is Field.TAGS:
            return await self._tags(viewer, values)
        if field_name is Field.PEOPLE:
            return await self._people(viewer, values)
        if field_name is Field.SITES:
            return await self._sites(viewer, values)
        if field_name is Field.COLLECTIONS:
            return await self._collections(viewer, values)
        if field_name is Field.PHOTO_SETS:
            return await self._photo_sets(viewer, values)
        if field_name is Field.SONGS:
            return await self._songs(viewer, values)
        return await self._folders(viewer, values)

    async def kept(self, viewer: Viewer, typed: Sequence[str]) -> list[str]:
        """`FilterEngine.kept`: typed filter texts by id. See `stored.typed_as_kept`."""
        from sift.slices.search.stored import typed_as_kept

        return await typed_as_kept(self, viewer, typed)

    async def shown(self, viewer: Viewer, typed: Sequence[str]) -> list[str]:
        """`FilterEngine.shown`: kept filter texts under today's names. See `stored.typed_as_shown`."""
        from sift.slices.search.stored import typed_as_shown

        return await typed_as_shown(self, viewer, typed)

    async def names_of(self, viewer: Viewer, field_name: Field, ids: list[str]) -> dict[str, str]:
        """The name each of these ids goes by NOW, for the ones this viewer may be shown."""
        wanted = _ids_among(ids)
        if not wanted:
            return {}
        if field_name is Field.TAGS:
            return {
                key: one.name
                for key, one in (await self._access.visible_tags(viewer, wanted)).items()
            }
        if field_name is Field.PEOPLE:
            return {
                key: one.name
                for key, one in (await self._access.visible_people(viewer, wanted)).items()
            }
        if field_name is Field.SITES:
            return {
                key: one.name
                for key, one in (await self._access.visible_sites(viewer, wanted)).items()
            }
        if field_name is Field.COLLECTIONS:
            return {
                one.id: one.name
                for one in await self._access.visible_collections(viewer)
                if one.id in wanted
            }
        if field_name is Field.PHOTO_SETS:
            return await self._photo_set_names(viewer, wanted)
        if field_name is Field.SONGS:
            return await self._song_names(viewer, wanted)
        return {
            key: one.name
            for key, one in (await self._access.visible_folders_of(viewer, wanted)).items()
        }

    async def _photo_set_names(self, viewer: Viewer, wanted: list[str]) -> dict[str, str]:
        named: dict[str, str] = {}
        for key in wanted:
            found = await self._access.visible_photo_set(viewer, key)
            if found is not None:
                named[key] = found.name
        return named

    async def _song_names(self, viewer: Viewer, wanted: list[str]) -> dict[str, str]:
        sung: dict[str, str] = {}
        for key in wanted:
            song = await self._access.visible_song(viewer, key)
            if song is not None:
                sung[key] = song.name
        return sung

    async def _tags(self, viewer: Viewer, values: list[str]) -> dict[str, tuple[str, ...]]:
        """Tag names to tag ids."""
        by_id = await self._access.visible_tags(viewer, _ids_among(values))
        found: dict[str, tuple[str, ...]] = {}
        for value in values:
            if value.strip() in by_id:
                found[value] = (value.strip(),)
                continue
            wanted = _folded(value)
            rows = await self._access.suggest_tags(viewer, value)
            found[value] = tuple(tag.id for tag in rows if _folded(tag.name) == wanted)
        return found

    async def _people(self, viewer: Viewer, values: list[str]) -> dict[str, tuple[str, ...]]:
        """Names to people, through the ONE resolver."""
        by_id = await self._access.visible_people(viewer, _ids_among(values))
        found: dict[str, tuple[str, ...]] = {}
        for value in values:
            if value.strip() in by_id:
                found[value] = (value.strip(),)
                continue
            match = await self._access.resolve_alias_targets(viewer, value)
            found[value] = match.person_ids
        return found

    async def _sites(self, viewer: Viewer, values: list[str]) -> dict[str, tuple[str, ...]]:
        by_id = await self._access.visible_sites(viewer, _ids_among(values))
        found: dict[str, tuple[str, ...]] = {}
        for value in values:
            if value.strip() in by_id:
                found[value] = (value.strip(),)
                continue
            wanted = _folded(value)
            rows = await self._access.suggest_sites(viewer, value)
            found[value] = tuple(row.id for row in rows if _folded(row.name) == wanted)
        return found

    async def _collections(self, viewer: Viewer, values: list[str]) -> dict[str, tuple[str, ...]]:
        # One read for however many values there are: the lister takes no prefix, so asking per
        # value would fetch the same rows again for each one.
        visible = await self._access.visible_collections(viewer)
        by_name: dict[str, list[str]] = {}
        known: set[str] = set()
        for collection in visible:
            by_name.setdefault(_folded(collection.name), []).append(collection.id)
            known.add(collection.id)
        return {
            value: (value.strip(),)
            if value.strip() in known
            else tuple(by_name.get(_folded(value), ()))
            for value in values
        }

    async def _photo_sets(self, viewer: Viewer, values: list[str]) -> dict[str, tuple[str, ...]]:
        """Photo-set names to ids, and an ID names exactly one."""
        # EACH VALUE IS LOOKED UP, not found in one page of the list. Matched against one page of
        # `list_photo_sets(limit=MAX_PAGE_SIZE)`, a set past that page would resolve to nothing and
        # its own page would read "Nothing in this set yet" over thousands of pictures, with no
        # error anywhere: an unknown name and an unlisted set are the same empty tuple. The id form
        # is one point read; the name form asks the list for that name and keeps the exact
        # matches, which is what `_folded` already decided a name means.
        found: dict[str, tuple[str, ...]] = {}
        for value in values:
            exact = await self._access.visible_photo_set(viewer, value)
            if exact is not None:
                found[value] = (exact.id,)
                continue
            page = await self._access.list_photo_sets(viewer, value, limit=MAX_PAGE_SIZE)
            wanted = _folded(value)
            found[value] = tuple(one.id for one in page.items if _folded(one.name) == wanted)
        return found

    async def _songs(self, viewer: Viewer, values: list[str]) -> dict[str, tuple[str, ...]]:
        """Song names to ids, and an ID names exactly one: the Photo Sets resolver's rule, for
        its reasons (`_photo_sets`). Two songs can share a name (two recordings AcoustID tells
        apart), so a name matches every song wearing it; the song's own page sends the id.
        """
        found: dict[str, tuple[str, ...]] = {}
        for value in values:
            exact = await self._access.visible_song(viewer, value.strip())
            if exact is not None:
                found[value] = (exact.id,)
                continue
            page = await self._access.list_songs(viewer, value, limit=MAX_PAGE_SIZE)
            wanted = _folded(value)
            found[value] = tuple(one.id for one in page.items if _folded(one.name) == wanted)
        return found

    async def _folders(self, viewer: Viewer, values: list[str]) -> dict[str, tuple[str, ...]]:
        """`in:` to folder ids. The subtree is the kernel's business, not this one's."""
        visible = await self._access.visible_folders(viewer)
        by_key: dict[str, list[str]] = {}
        known: set[str] = set()
        for folder in visible:
            by_key.setdefault(_folded(folder.rel_path), []).append(folder.id)
            by_key.setdefault(_folded(folder.name), []).append(folder.id)
            known.add(folder.id)
        found: dict[str, tuple[str, ...]] = {}
        for value in values:
            wanted = value.strip()
            # The id first. A folder whose NAME is somebody else's id is a case that cannot arise
            # (an id is not a name a filesystem produces), and if it ever did, the id is the
            # more specific answer of the two.
            if wanted in known:
                found[value] = (wanted,)
                continue
            found[value] = tuple(by_key.get(_folded(wanted).strip("/"), ()))
        return found

    # --- building the conditions -------------------------------------------------------------

    def _build(
        self,
        node: Node,
        found: dict[Field, dict[str, tuple[str, ...]]],
        folders: list[tuple[str, ...]],
        *,
        now: int,
        admin: bool,
    ) -> Constraint:
        """One node of the query as one node of the constraints. Shape in, shape out."""
        if isinstance(node, Group):
            parts = tuple(
                self._build(part, found, folders, now=now, admin=admin) for part in node.parts
            )
            return AllOf(parts) if node.op is Op.ALL else AnyOf(parts)
        if isinstance(node, Negated):
            return Not(self._build(node.part, found, folders, now=now, admin=admin))
        if isinstance(node, Presence):
            asked = Where(_PRESENCE[node.field])
            return asked if node.present else Not(asked)

        # A term naming nothing cannot describe an asset, whichever field it is.
        if not node.value.strip():
            return AnyOf()
        # `sharing:` describes somebody's decisions rather than the file, so it is an admin-only
        # question. For anybody else it matches nothing, which is the same answer this module
        # already gives for a name the asker may not be told about, and it is the safe direction:
        # a filter that returns nothing, never one that quietly widens. `unnamed_face:` is a
        # question about faces nobody named, which only an admin is shown, for the same reason.
        if node.field in (Field.SHARING, Field.UNNAMED_FACE) and not admin:
            return AnyOf()
        if node.field is Field.LIKE:
            # The ranking `_lookalikes` asked for, already checked against this viewer.
            return Where("assets", found.get(Field.LIKE, {}).get(node.value, ()))
        if node.field not in ENTITY_FIELDS:
            return scalar(node, now=now)

        ids = found[node.field][node.value]
        if node.field is Field.IN:
            # The subtree expansion happens once for the statement, so a folder condition binds
            # WHICH of the expanded groups it means rather than the ids themselves.
            folders.append(ids)
            return Where("folder", (len(folders) - 1,))
        return Where(_DIMENSION[node.field], ids)


def _closest(
    alike: Mapping[str, tuple[tuple[str, float], ...]],
) -> tuple[tuple[str, float], ...]:
    """Every file the `like:` rankings named, closest first, each at its nearest distance."""
    best: dict[str, float] = {}
    for pairs in alike.values():
        for asset_id, apart in pairs:
            if asset_id not in best or apart < best[asset_id]:
                best[asset_id] = apart
    return tuple(sorted(best.items(), key=lambda pair: (pair[1], pair[0])))


def _text(text: str | None) -> tuple[Constraint, ...]:
    """Free text as conditions: the indexed half, the scanned half, or neither."""
    if text is None:
        return ()
    conditions: list[Constraint] = []
    if fts_match(text) is not None:
        conditions.append(Where("text_match"))
    if fts_contains(text) is not None:
        conditions.append(Where("text_contains"))
    return tuple(conditions)


def _and(where: Constraint, text: tuple[Constraint, ...]) -> Constraint:
    """The filters and the free text, which all have to hold."""
    if not text:
        return where
    if isinstance(where, AllOf):
        return AllOf(where.parts + text)
    return AllOf((where, *text))
