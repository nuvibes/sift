# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a Photo Set is, as every feature that makes one or says what makes one reads it.

A leaf. The floor is a fact about the thing itself rather than about one way of deriving it, and
three features say it in words: the photo-sets feature's two switches (folders, ZIP files) and the
downloads feature's switch for galleries. A feature may not import another, so the number lives
here, under all three, and each sentence is built from it: a floor raised here is the floor every
sentence says, with nothing written out by hand to fall behind it.
"""

from __future__ import annotations

#: How many pictures make a shoot.
#:
#: Ten. Two pictures arriving together is a pair of files, and a low floor fills the wall with
#: near-pairs nobody meant to make as sets. Deriving too eagerly is worse than deriving too late,
#: because a set that should not exist has to be found and deleted by hand while a missing one is
#: one press of the manual verb. The photo-sets feature's floor job (`jobs.dissolve_under_floor`)
#: takes a raised floor to sets Sift has already made.
MIN_PICTURES = 10
