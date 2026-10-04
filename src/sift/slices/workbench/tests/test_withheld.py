# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a card may draw of a file: nothing of one the viewer is not shown or the vault holds back,
and no picture of one whose picture has not been made yet."""

from __future__ import annotations

from sift.kernel.workbench import ASSET, FACE, Preview
from sift.slices.workbench.router import _Tokens

SHOWN = "01KZCNTD0PENF17E0000000000"
HELD = "01KZCNTDSHVTF17E0000000000"
UNSEEN = "01KZCNTDVNSEENF17E00000000"


def test_a_card_draws_only_the_files_it_may_show() -> None:
    art = _Tokens(face="1", files={SHOWN: "a", HELD: "b"}, withheld=frozenset({HELD}))

    assert art.draws(Preview(kind=ASSET, id=SHOWN))
    assert not art.draws(Preview(kind=ASSET, id=HELD))
    assert not art.draws(Preview(kind=ASSET, id=UNSEEN))
    assert art.draws(Preview(kind=FACE, id=UNSEEN))


MAKING = "01KZCNTDMAKNGF17E000000000"


def test_a_card_leaves_out_a_picture_that_is_not_made_yet() -> None:
    """The queues' own previews are not asked whether a thumbnail exists; this lock is. The file is
    still one this viewer may be shown; only its picture is left out."""
    art = _Tokens(face="1", files={SHOWN: "a", MAKING: None}, unmade=frozenset({MAKING}))

    assert art.draws(Preview(kind=ASSET, id=SHOWN))
    assert not art.draws(Preview(kind=ASSET, id=MAKING))
    assert art.shows(Preview(kind=ASSET, id=MAKING))
    assert art.draws(Preview(kind=FACE, id=MAKING))
