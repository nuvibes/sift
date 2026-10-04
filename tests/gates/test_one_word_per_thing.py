# SPDX-License-Identifier: AGPL-3.0-or-later
"""The interface calls each thing by ONE name, and it is the name the thing has.

The failure is a sentence that reaches for a plain word meaning roughly the right thing and ships a
second name for an object that already had one: Site (never platform), username for a name on a
Site (never account or handle; "account" only on the sign-in screens, `SIGN_IN_SCREENS`), Photo Set
with its capitals, Delete when the thing ceases to exist and Remove when only a membership or a
setting changes. "Username" names two things on purpose: a sign-in name on the login screen and
Settings > Users, a person's name on a Site everywhere else; nothing else may.

This file reads the SERVER's copy (every registered setting's words), proves the reader on the
sentences that shipped, and holds what the client gate (`frontend/scripts/check_vocabulary.js`)
does not: Photo Set's capitals and the quarantine files read whole. Comments and docstrings are
exempt: they define the terms. The words live in `tests/gates/data/vocabulary.json`; a pair goes in
when the wrong word has shipped, not when somebody can imagine it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.gates import client_source, vocabulary

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
CLIENT = REPO / "frontend" / "src"

#: The coined word, and the thing it is a second name for (`wrong_words`): `platform`; the
#: stash-boxes' own words for things Sift names (`scene` also reads as one PART of a video);
#: `fetch` for downloading; `shall`; and `account` and `handle` for a username.
WRONG_WORD: dict[str, str] = vocabulary.wrong_words()

#: Where "account" means what somebody signs in to Sift WITH: the login screens, Settings > Users,
#: and `routes/setup`, the first-run screen that makes the first sign-in user.
SIGN_IN_SCREENS: tuple[str, ...] = vocabulary.sign_in_screens()

#: The words `SIGN_IN_SCREENS` may say, and nothing else may.
_SIGN_IN_WORDS = vocabulary.sign_in_words()

#: The screens whose subject is what a Site wants before it will hand over a file: the downloads
#: route, the Cookies sheet, and the Sites pane with its search sidecar.
_COOKIE_SCREENS: tuple[str, ...] = vocabulary.scope("cookie_screens")

#: The Organize tabs, the pile and person screens, and the strip of faces under a file.
_FACE_SCREENS: tuple[str, ...] = vocabulary.scope("face_screens")

#: The face screens where "remove" can only mean destroying a face. Narrower than `_FACE_SCREENS`:
#: `components/organize` also holds the duplicate-copies and quarantine panels, so the organize
#: half is named file by file, and a new face panel there must be added.
_FACE_DELETE_SCREENS: tuple[str, ...] = vocabulary.scope("face_delete_screens")

#: The name the uploaded-cover question is read under, among the destroying screens below.
_COVER_QUESTION_WHERE = "entity/EntityHeader:uploaded-cover-question"

#: The screens outside the face screens whose "Remove" destroyed something, one file each: every
#: folder they sit in also holds a correct Remove. `StashBoxes.` rather than the stem, which would
#: reach `StashBoxesPane`; `_COVER_QUESTION_WHERE` is no file.
_DESTROYING_SCREENS: tuple[str, ...] = vocabulary.scope("destroying_screens")

#: Words wrong only in one PART of the interface, as `(wrong, right, where)` rows, because one word
#: can be wrong in two parts for two reasons ("set aside" is a second name for quarantine on the
#: downloads screens AND for a discarded pile on the face screens). Elsewhere they are ordinary
#: English, and a blanket ban would need an exemption per sentence. A face pile put away is
#: Discarded (the stored status stays `ignored`, see `SET_ASIDE` in the faces slice's `queue.py`). A
#: thing that ceases to exist is Deleted on every screen that destroys one, and "Forget" is refused
#: on the Cookies sheet. What a Site needs before it shows its files is cookies, never a login: Sift
#: never asks for a Site's password. `Sites.` reaches the pane's search sidecar too.
SCOPED_WRONG_WORD: tuple[tuple[str, str, tuple[str, ...]], ...] = vocabulary.scoped_wrong_words()

#: Sentences where the plain word is right, by phrase rather than by file, so an exemption covers
#: only the sentence argued for: the stash-box pane saying a service calls a Site a studio.
ALLOWED: tuple[str, ...] = vocabulary.allowed_sentences()

#: A Photo Set is one of Sift's objects, as a Site is, and the rail spells it "Photo Sets". A
#: capitalisation rule, so it cannot live in `WRONG_WORD`, which matches case-insensitively. Only
#: the spaced form: `/photo-sets` in a `title` is a route, not copy.
_MISCAPITALISED_PHOTO_SET = re.compile(r"\bphoto sets?\b", re.IGNORECASE)
_CORRECT_PHOTO_SET = re.compile(r"\bPhoto Sets?\b")

#: Attributes that carry copy somebody reads. `alt` and `aria-label` too: read aloud is read.
_COPY_ATTRS = (
    "label",
    "help",
    "title",
    # The words that NAME a thing: "1-20 of 34 photo sets" is written as `noun="photo sets"`.
    "noun",
    "heading",
    "deleteWord",
    "placeholder",
    "empty",
    "failed",
    "confirmLabel",
    "alt",
    "aria-label",
)

#: A copy attribute or a copy FIELD, `=` or `:`, in either quote: half the copy is written as
#: `label: 'words'` in an object literal. A one-word value is read too, since carrying copy is
#: the attribute's whole job.
_ATTR = re.compile(
    r"\b(?:"
    + "|".join(re.escape(name) for name in _COPY_ATTRS)
    + r")\s*[:=]\s*(?:\"([^\"]*)\"|'([^']*)')"
)
#: Text between tags, with anything interpolated left out: `{site.name}` is data, not copy.
_TEXT = re.compile(r">([^<>{}]{3,})<")
#: What a toast says.
_TOAST = re.compile(r"""toasts\.show\(\s*(['"])(.*?)\1""", re.DOTALL)
#: An interpolated expression: what it evaluates to is copy, what it is NAMED is not.
_INTERPOLATED = re.compile(r"\{[^{}]*\}")
#: A component's script and styles: `api.get<Thing>(` and a CSS child combinator look like tags.
_NOT_MARKUP = re.compile(r"<(script|style)\b.*?</\1>", re.DOTALL)

#: One `{...}` in the markup with one level of nesting, for a ternary of template literals.
_EXPRESSION = re.compile(r"\{(?:[^{}]|\{[^{}]*\})*\}", re.DOTALL)

#: A quoted run inside one of those: a single-quoted, double-quoted or backticked literal.
_LITERAL = re.compile(
    r"'([^'\\]*(?:\\.[^'\\]*)*)'|\"([^\"\\]*(?:\\.[^\"\\]*)*)\"|`([^`\\]*(?:\\.[^`\\]*)*)`",
    re.DOTALL,
)

#: What a template literal puts INTO a sentence, which is data for the reason `_INTERPOLATED` is.
_SUBSTITUTED = re.compile(r"\$\{[^{}]*\}")


def _written_into_an_expression(markup: str) -> list[str]:
    """Every sentence written as a STRING inside a markup expression.

    `_TEXT` skips a paragraph whose whole content is one interpolation, which is how a screen says
    one of two things. Only literals with a SPACE are read: a one-word literal is a class, a tone
    or a key far more often than copy.
    """
    found: list[str] = []
    for block in _EXPRESSION.finditer(markup):
        for match in _LITERAL.finditer(block.group(0)):
            written = next(group for group in match.groups() if group is not None)
            plain = _SUBSTITUTED.sub(" ", written)
            if " " in plain.strip():
                found.append(plain)
    return found


def _phrases(source: str, *, has_markup: bool = True) -> list[str]:
    """Every run of words a person reads on the screen, from one file.

    Not read: an interpolation (`Put {nameOf(site.scope)} back` names a variable), angle brackets
    inside `<script>` or `<style>` (a generic type parameter is not a tag), and text between tags
    in a file with no markup. Attributes and toasts are read in a `.ts` file too.
    """
    without_comments = re.sub(r"<!--.*?-->", " ", source, flags=re.DOTALL)
    without_comments = re.sub(r"/\*.*?\*/", " ", without_comments, flags=re.DOTALL)
    without_comments = re.sub(r"^\s*//.*$", " ", without_comments, flags=re.MULTILINE)
    markup = _NOT_MARKUP.sub(" ", without_comments) if has_markup else ""
    return [
        *(
            _INTERPOLATED.sub(" ", match.group(1) if match.group(1) is not None else match.group(2))
            for match in _ATTR.finditer(without_comments)
        ),
        *(match.group(1) for match in _TEXT.finditer(markup)),
        *_written_into_an_expression(markup),
        *(match.group(2) for match in _TOAST.finditer(without_comments)),
    ]


def _wrong_words_for(where: str) -> dict[str, str]:
    """The words that are wrong in this file: the ones wrong everywhere, plus this area's own."""
    words = dict(WRONG_WORD)
    if any(screen in where for screen in SIGN_IN_SCREENS):
        for word in _SIGN_IN_WORDS:
            words.pop(word, None)
    for wrong, right, folders in SCOPED_WRONG_WORD:
        if any(folder in where for folder in folders):
            words[wrong] = right
    return words


def offences(source: str, where: str = "") -> list[tuple[str, str, str]]:
    """`(wrong word, right word, the sentence)` for each coined name in readable copy.

    `where` picks the scoped words and whether the file holds markup; "" reads as a component, the
    strictest reading.
    """
    return offences_in(_phrases(source, has_markup=not where.endswith(".ts")), where)


def offences_in(phrases: list[str], where: str = "") -> list[tuple[str, str, str]]:
    """The same check over copy already separated from its source.

    Handing a bare sentence to `offences` is silently empty: it finds no `>text<`, attribute or
    toast in it.
    """
    words = _wrong_words_for(where)
    found: list[tuple[str, str, str]] = []
    for phrase in phrases:
        if any(allowed in phrase for allowed in ALLOWED):
            continue
        for wrong, right in words.items():
            if re.search(vocabulary.word_pattern(wrong), phrase, re.IGNORECASE):
                found.append((wrong, right, phrase.strip()))
    return found


def miscapitalised(phrases: list[str]) -> list[str]:
    """Every sentence that names a Photo Set and does not spell it "Photo Set"; takes phrases."""
    found: list[str] = []
    for phrase in phrases:
        for match in _MISCAPITALISED_PHOTO_SET.finditer(phrase):
            if _CORRECT_PHOTO_SET.fullmatch(match.group(0)):
                continue
            found.append(phrase.strip())
            break
    return found


def _settings_copy() -> list[tuple[str, str]]:
    """Every registered setting's label, help and folded-away detail, with its key.

    Imported rather than read as text, so a string built from a constant is checked as read.
    """
    import sift.main  # noqa: F401 (imported for the side effect: every slice registers its own)
    from sift.kernel.settings_registry import registered_settings

    written: list[tuple[str, str]] = []
    for key, one in sorted(registered_settings().items()):
        for sentence in (one.label, one.help, getattr(one, "disclosure", None)):
            if sentence:
                written.append((key, str(sentence)))
        for word in getattr(one, "choice_labels", None) or ():
            written.append((key, str(word)))
    return written


@pytest.mark.regression
def test_a_settings_screen_uses_the_same_words_as_the_rest_of_the_interface() -> None:
    """Every registered setting's copy uses the interface's words: it is declared in Python, not the
    client."""
    complaints: list[str] = []
    written = _settings_copy()
    assert len(written) > 200, (
        f"only {len(written)} sentences were registered, which cannot be right"
    )

    for key, sentence in written:
        for wrong, right, phrase in offences_in([sentence], "settings.py"):
            complaints.append(f"{key}: {wrong!r} where the word is {right}\n      {phrase[:110]}")

    assert not complaints, (
        "\nA settings screen has a second name for something the rest of the interface names.\n\n"
        "These strings are declared in Python and drawn on a settings pane, so they are copy in\n"
        "every sense that matters, and they are the copy nobody re-reads.\n\n  "
        + "\n  ".join(complaints)
        + "\n"
    )


@pytest.mark.regression
def test_the_interface_calls_a_photo_set_a_Photo_Set() -> None:
    """Every screen spells a Photo Set "Photo Set"."""
    complaints: list[str] = []
    for path in client_source(CLIENT, ".svelte", ".ts"):
        if path.name == "schema.d.ts":
            continue
        where = str(path.relative_to(CLIENT)).replace("\\", "/")
        source = path.read_text(encoding="utf-8")
        for phrase in miscapitalised(_phrases(source, has_markup=not where.endswith(".ts"))):
            complaints.append(f"{path.relative_to(REPO)}:\n      {phrase[:110]}")

    assert not complaints, (
        "\nA Photo Set is spelled two ways.\n\n"
        'It is one of Sift\'s objects and the rail has always called it "Photo Sets". A sentence\n'
        "that calls it a photo set reads perfectly well, which is exactly how one thing ends up\n"
        "with two names on screens that sit beside each other.\n\n  "
        + "\n  ".join(complaints)
        + "\n"
    )


@pytest.mark.regression
def test_a_settings_screen_spells_a_Photo_Set_the_same_way() -> None:
    """The same rule over the sentences that are declared in Python and drawn on a pane."""
    complaints = [
        f"{key}: {phrase[:110]}"
        for key, sentence in _settings_copy()
        for phrase in miscapitalised([sentence])
    ]
    assert not complaints, (
        "\nA settings pane spells a Photo Set its own way.\n\n  " + "\n  ".join(complaints) + "\n"
    )


def test_the_photo_set_rule_catches_every_spelling_that_was_in_the_tree() -> None:
    """All five wrong ones, and neither of the two right ones."""
    for wrong in (
        "<p>Nothing in this photo set yet.</p>",
        '<Empty title="No photo sets" />',
        "<h2>Photo set</h2>",
        "<span>Photo sets</span>",
    ):
        assert miscapitalised(_phrases(wrong)), f"not caught: {wrong}"

    for right in ("<h2>Photo Set</h2>", '<Empty title="No Photo Sets yet" />'):
        assert miscapitalised(_phrases(right)) == [], right

    # A comment defines the term and is not copy, the same as everywhere else in this file.
    assert miscapitalised(_phrases("<!-- a photo set is a group of pictures -->")) == []

    # The identifier and the route are not copy, and they do not change.
    assert miscapitalised(["photo_set", "/photo-sets", "photoSetOpen"]) == []


def test_a_comment_is_not_copy() -> None:
    """Comments define the term, so all three comment syntaxes are stripped before reading.

    The old word is planted here because a comment is where it may still be written.
    """
    for source in (
        "<!-- a platform is a site -->",
        "/* a platform is a site */",
        "\t// a platform is a site\n",
    ):
        assert offences(source) == [], source


def test_the_words_that_actually_shipped_are_all_caught() -> None:
    """The Site rename's real sentences, with the old word, are caught as an attribute, a toast and
    text between tags."""
    shipped = [
        '<Field label="Add a platform" hideLabel>',
        'empty="Nothing has come from this platform yet."',
        "toasts.show('That platform could not be added.', { tone: 'error' })",
        "<h1>Platform connections</h1>",
        '<p class="empty">That platform is not here.</p>',
    ]
    for source in shipped:
        assert offences(source), f"this shipped and the gate does not see it: {source}"


#: The files whose whole subject is quarantine, where "set aside" has no correct use. One by one,
#: because `components/organize` holds face screens too.
QUARANTINE_FILES: tuple[str, ...] = (
    "frontend/src/lib/components/organize/QuarantinePanel.svelte",
    "frontend/src/lib/settings-ui/refused.svelte.ts",
    "frontend/src/lib/settings-ui/Maintenance.svelte",
    "frontend/src/routes/downloads/DownloadRow.svelte",
    "src/sift/slices/download/endings.py",
    "src/sift/slices/download/landing.py",
    "src/sift/slices/library_roots/router.py",
)

_SET_ASIDE = re.compile(r"\bset[- ]aside\b", re.IGNORECASE)


def test_nothing_about_quarantine_calls_it_setting_aside() -> None:
    """The quarantine files, read whole, never call it setting aside.

    `offences` cannot see these sentences: a `<script>` return, a template literal from a plain
    function, and a label passed through an interpolation. Reading whole files, comments included,
    is fine because none of them is about a face pile.
    """
    complaints = [
        f"{name}:{number}: {line.strip()[:110]}"
        for name in QUARANTINE_FILES
        for number, line in enumerate((REPO / name).read_text(encoding="utf-8").splitlines(), 1)
        if _SET_ASIDE.search(line)
    ]

    assert not complaints, (
        "\nQuarantine has picked up a second name.\n\n"
        "A file is quarantined; a face pile is set aside. These files are about the first, and\n"
        "the phrase reads perfectly well in each of them, which is exactly how one thing ended up\n"
        "with two names on screens that sit beside each other.\n\n  "
        + "\n  ".join(complaints)
        + "\n"
    )


def test_the_quarantine_screens_are_watched_for_the_word_that_shipped() -> None:
    """The shipped quarantine sentence is caught, and on a face screen the words are refused as a
    PILE, never as quarantine: the rule is about files."""
    shipped = (
        '<p class="empty">Nothing has been set aside.</p>',
        'label="Files set aside"',
    )
    for source in shipped:
        where = "lib/components/organize/QuarantinePanel.svelte"
        assert offences(source, where), f"this shipped and the gate does not see it: {source}"

    face_screen = offences('confirmLabel="Set aside"', "lib/components/faces/PileDetail.svelte")
    assert [(wrong, right) for wrong, right, _ in face_screen] == [("set aside", "discarded")]


def test_a_face_pile_put_away_is_discarded_on_every_face_screen() -> None:
    """Every earlier word for discarding a face pile is refused where faces are drawn, the strip
    under a file included, and nowhere else: ignoring is ordinary English elsewhere."""
    for where in (
        "lib/components/faces/FaceGroups.svelte",
        "lib/components/organize/FaceGroupsPanel.svelte",
        "lib/components/FacesInThis.svelte",
    ):
        for source, word in (
            ('confirmLabel="Ignore"', "ignore"),
            ("<p>Nothing has been ignored.</p>", "ignored"),
            ("toasts.show('That group is dismissed')", "dismissed"),
        ):
            assert [(wrong, right) for wrong, right, _ in offences(source, where)] == [
                (word, "discarded" if word.endswith("ed") else "discard")
            ], f"{where}: {source}"
        assert offences('confirmLabel="Discard"', where) == []

    assert offences('label="Ignore case"', "lib/settings-ui/Search.svelte") == []


def test_a_face_that_ceases_to_exist_is_deleted_on_every_face_screen() -> None:
    """The words that shipped on the face screens before Delete are caught; Delete passes.

    A chip's cross takes a tag OFF a file and the duplicate-copies panel has undecided Removes, so
    both are asserted untouched.
    """
    for where in (
        "lib/components/faces/FaceGroups.svelte",
        "lib/components/faces/verbs.ts",
        "lib/components/FacesInThis.svelte",
        "lib/components/organize/IdentifiedForPerson.svelte",
    ):
        for source, word in (
            ('confirmLabel="Remove"', "remove"),
            ("label: 'Remove',", "remove"),
            ("toasts.show('That face has been removed', { tone: 'success' })", "removed"),
            ("<p>{busy ? 'Removing the faces now' : ''}</p>", "removing"),
        ):
            if source.startswith("<p>") and where.endswith(".ts"):
                continue
            assert [(wrong, right) for wrong, right, _ in offences(source, where)] == [
                (word, word.replace("remov", "delet"))
            ], f"{where}: {source}"
        assert offences('confirmLabel="Delete permanently"', where) == []

    assert offences('confirmLabel="Remove"', "lib/components/common/Chip.svelte") == []


def test_a_thing_that_ceases_to_exist_is_deleted_on_every_screen_that_destroys_one() -> None:
    """The words that shipped on screens that destroy a thing are caught, one screen each.

    Planted in every shape the rule reads, plus the Cookies sheet's own second word, Forget.
    """
    shipped = (
        ("lib/settings-ui/GraphicsCard.svelte", 'label="Remove graphics card support"', "remove"),
        ("lib/settings-ui/StashBoxes.svelte", "<Button>Remove</Button>", "remove"),
        ("lib/components/organize/CopiesPanel.svelte", 'title="Remove this copy?"', "remove"),
        ("lib/components/downloads/CookiesSheet.svelte", 'confirmLabel="Forget"', "forget"),
        ("lib/settings-ui/Users.svelte", "label: 'Remove',", "remove"),
        ("lib/settings-ui/Tunnels.svelte", 'aria-label="Remove the tunnel {item.name}"', "remove"),
        ("lib/settings-ui/Semantic.svelte", "toasts.show('The index has been removed')", "removed"),
        ("lib/settings-ui/Maintenance.svelte", "<p>Before Sift removes them</p>", "removes"),
        ("lib/settings-ui/Users.search.ts", "label: 'Removing a guest',", "removing"),
    )
    for where, source, word in shipped:
        assert [wrong for wrong, _right, _ in offences(source, where)] == [word], (
            f"{where}: {source}"
        )

    for where, _source, _word in shipped:
        assert offences('confirmLabel="Delete permanently"', where) == [], where
    # Forget is ordinary English elsewhere: "Forget what was learned" on the faces settings.
    assert offences("name: 'Forget what was learned',", "lib/settings-ui/Faces.search.ts") == []


def test_the_membership_removes_keep_their_word() -> None:
    """A Remove that takes something OFF something, where it stays, passes on its real screen.

    A scope that crept over one of them would take the right word off the right screen.
    """
    kept = (
        ("lib/components/common/Chip.svelte", 'confirmLabel="Remove"'),
        ("lib/components/common/TagChip.svelte", 'removeLabel="Remove {name}"'),
        (
            "lib/components/FileBandRows.svelte",
            "removeLabel={one.kind === 'collection' ? `Remove from the collection ${one.name}`"
            " : `Remove from the Photo Set ${one.name}`}",
        ),
        (
            "lib/components/common/Heart.svelte",
            "aria-label={favorite ? 'Remove from favorites' : 'Add to favorites'}",
        ),
        ("lib/grid/verbs.ts", "label: 'Remove from favorites',"),
        ("lib/components/DeleteDialog.svelte", 'name="Remove from Sift"'),
        ("lib/library/LibraryScreen.svelte", 'confirmLabel="Remove"'),
        ("routes/collections/[id]/+page.svelte", "label: 'Remove from this collection',"),
        ("routes/downloads/+page.svelte", "label: 'Remove from the list',"),
        ("lib/components/record/FieldEditor.svelte", 'aria-label="Remove {entry}"'),
        ("lib/components/theater/CellMenu.svelte", 'label="Remove this feed"'),
        ("lib/components/entity/EntityHeader.svelte", 'aria-label="Remove the cover"'),
    )
    for where, source in kept:
        assert offences(source, where) == [], f"{where}: {source}"


#: Found by what opens it rather than by its words, so a rewording cannot hide it.
_COVER_QUESTION = re.compile(r"<ConfirmDialog\s+bind:open=\{confirmRemove\}.*?/>", re.DOTALL)


def test_an_uploaded_cover_is_deleted_in_its_own_question() -> None:
    """An uploaded cover's bytes are dropped when it stops being the cover, so its question says
    Delete; read on the DIALOG only, since `EntityHeader` also correctly says Remove."""
    source = (CLIENT / "lib" / "components" / "entity" / "EntityHeader.svelte").read_text(
        encoding="utf-8"
    )
    found = _COVER_QUESTION.search(source)
    assert found, (
        "the uploaded-cover question is not where this looks for it (bind:open={confirmRemove})"
    )
    assert "Delete" in found.group(0), found.group(0)
    assert offences_in(_phrases(found.group(0)), _COVER_QUESTION_WHERE) == [], (
        f"the uploaded-cover question says Remove again:\n{found.group(0)}"
    )

    # Known positive: the question as it shipped is refused by the same reading.
    shipped = '<ConfirmDialog\n\tbind:open={confirmRemove}\n\ttitle="Remove this cover?"\n/>'
    planted = _COVER_QUESTION.search(shipped)
    assert planted and offences_in(_phrases(planted.group(0)), _COVER_QUESTION_WHERE)


def test_interpolated_data_is_not_read_as_copy() -> None:
    """A variable may still be NAMED the old word. It is code, not something anybody reads."""
    assert offences("<span>{platform.name}</span>") == []


def test_a_sentence_written_inside_an_expression_is_copy() -> None:
    """A paragraph whose whole content is a ternary is still a paragraph: `_TEXT` skips it."""
    assert offences("<p>{ready ? `Add a platform` : `No platforms yet`}</p>")
    assert offences("<p>{ready ? 'Nothing has come from this platform yet.' : ''}</p>")


def test_a_word_inside_an_expression_is_not_read_as_copy() -> None:
    """A literal with no space in it is a class, a tone or a key, not a sentence."""
    assert offences("<div class={wide ? 'site' : 'platform'}></div>") == []


def test_what_an_expression_substitutes_is_data_like_any_other() -> None:
    """`${platform.name}` is the row's own name, exactly as `{platform.name}` is."""
    assert offences("<p>{`Nothing on ${platform.name} yet, so far as this knows`}</p>") == []


def test_the_cookies_screens_are_watched_for_the_words_that_shipped() -> None:
    """The real sentences the Cookies screens held, in every shape they were written, are caught."""
    where = "routes/downloads/DownloadRow.svelte"
    for source in (
        "label: 'Waiting for login',",
        "<span>{blocked ? 'This download is waiting for a login' : ''}</span>",
    ):
        assert offences(source, where), f"this shipped and the gate does not see it: {source}"

    where = "settings-ui/Sites.svelte"
    for source in (
        'title="Add a login"',
        'label="Login (cookies)"',
        'label="Saved logins"',
        'aria-label="Remove the {item.site} login"',
        'label="Needs signing in again"',
        "<Button>Save login</Button>",
        "title={removing ? `Remove the ${removing.site} login?` : 'Remove this login?'}",
    ):
        assert offences(source, where), f"this shipped and the gate does not see it: {source}"

    # The search sidecar, which names the same block for the settings search.
    assert offences(
        "help: 'Sign in to a Site once and Sift keeps the session.'", "settings-ui/Sites.search.ts"
    )


def test_the_sign_in_screens_keep_their_own_words() -> None:
    """Signing in to Sift is a real act, so the sign-in screens keep their words."""
    for where in (
        "routes/login/+page.svelte",
        "routes/+layout.svelte",
        "settings-ui/Privacy.svelte",
    ):
        assert offences("<h1>Sign in</h1>", where) == [], where
        assert offences('heading="Signing in"', where) == [], where
        assert offences("<p>Your login is how you get back in.</p>", where) == []


def test_the_words_the_cookies_screens_are_being_renamed_to_are_legal() -> None:
    """The words those screens use now pass there: a scope too wide would refuse its own fix."""
    for source in (
        "label: 'Waiting for cookies',",
        "label: 'Already in library',",
        "label: 'Skipped',",
        "<Button>Add cookies</Button>",
        "<h2>Cookies</h2>",
        "<p>Ends 14 November. Last used yesterday.</p>",
        "<p>Read: 14 cookies for one-site.test, until 2 December</p>",
        '<Empty title="No cookies saved yet" />',
    ):
        for where in ("routes/downloads/DownloadRow.svelte", "settings-ui/Sites.svelte"):
            assert offences(source, where) == [], f"{where}: {source}"


def test_a_name_on_a_site_is_a_username_on_every_screen() -> None:
    """The words that shipped for a username are caught on ordinary screens."""
    for source, where in (
        ('title: "Handles Waiting"', "components/organize/X.ts"),
        ("<h3>Handles</h3>", "components/record/Fields.svelte"),
        ("<p>Name accounts from pictures</p>", "settings-ui/Importing.svelte"),
        ("<span>an account that is gone</span>", "components/common/HistoryRow.svelte"),
    ):
        assert offences(source, where), f"{where}: {source}"


def test_the_sign_in_screens_may_still_say_account_and_nowhere_else() -> None:
    """The sign-in screens alone may say "Your account"; "username" passes everywhere."""
    for where in ("routes/login/+page.svelte", "settings-ui/Users.svelte"):
        assert offences("<p>Your account is how you get back in.</p>", where) == [], where
    assert offences("<p>Your account is how you get back in.</p>", "routes/browse/+page.svelte")
    for where in ("routes/login/+page.svelte", "components/organize/UsernamePanel.svelte"):
        assert offences("<label>Username</label>", where) == [], where
    assert offences("<p>Handles</p>", "settings-ui/Users.svelte"), "handle has no sign-in sense"
