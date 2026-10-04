# SPDX-License-Identifier: AGPL-3.0-or-later
"""The face numbers, in one place, with what each one is for.

Every threshold is measured, not chosen: a number moved without a measurement makes matching quietly
worse, and no test can catch it. Several are also settings, declared next door; this holds the
default and the bound a value is validated against.
"""

from __future__ import annotations

# --- Matching -----------------------------------------------------------------------------------
#
# Similarity is the dot product of two unit-length embeddings: -1 to 1, a face against itself 1.0.
# Two single faces sit on a lower scale than a face against a person's blended gallery, which is why
# the clustering numbers below differ from the matching ones.

#: Attach a person without asking, while Sift has little to go on about them: above the point of
#: best accuracy, since an attribution nobody was asked about is the one nobody checks. The starting
#: bar (see `bar_for`).
AUTO_APPLY_CONFIDENCE = 0.60

#: The same bar once a person is well described: half a percent of certainty buys back the five
#: percent of a person's appearances the starting bar misses.
STRONG_APPLY_CONFIDENCE = 0.55

#: Offered for somebody to agree to. Below this a match is not shown: a list of everything Sift
#: half-noticed is a list nobody reads to the end.
SUGGEST_CONFIDENCE = 0.45

#: The bar at which nothing is attached on its own: the top of the setting's scale, whose help
#: promises it means always being asked, so nothing below may move it.
ALWAYS_ASK = 1.0


def bar_for(references: int, *, bar: float = AUTO_APPLY_CONFIDENCE) -> float:
    """How sure a match has to be before it is attached without asking, for THIS person.

    A comparison is against a person's blended references, so one score means less for somebody
    described by two pictures than by twenty; one bar for both costs the well-described person her
    own appearances. So under `STRONG_REFERENCES` it is `bar`, and at or above it the bar relaxes by
    the gap between the two defaults, applied to whatever the install is set to. It never falls
    below the suggest line, and a bar at the top of the scale means never and stays never, as the
    setting's help promises.
    """
    if bar >= ALWAYS_ASK or references < STRONG_REFERENCES:
        return bar
    return max(SUGGEST_CONFIDENCE, bar - (AUTO_APPLY_CONFIDENCE - STRONG_APPLY_CONFIDENCE))


#: How many groups a person's references are clustered into for matching. One: at ten to twenty
#: references the average describes a person better than any subgroup.
MATCH_GROUPS = 1

#: The most groups when `MATCH_GROUPS` is raised: past four a group is described by too few faces.
MAX_MATCH_GROUPS = 4

# --- Clustering ---------------------------------------------------------------------------------

#: How alike two unidentified faces must be to share a pile, averaged across the two piles. Set to
#: over-split: a person in three piles is a merge away from fixed, while two people in one silently
#: files somebody under the wrong person.
PILE_JOIN = 0.50

#: The fewest faces a pile is worth showing. One: single faces can be most of what was found, and a
#: screen that omits them cannot be told from one with nothing left to name.
MIN_PILE_SIZE = 1

#: Rejoins two runs of the same face within one file, split by position because samples are seconds
#: apart, so an appearance count comes out right.
TRACKLET_MERGE = 0.50

# --- Tracking -----------------------------------------------------------------------------------

#: How much two boxes in consecutive samples overlap to be one face continuing. The only test: a
#: nearness fallback joins two people standing close, and a face that left its box is rejoined by
#: `TRACKLET_MERGE`.
TRACK_OVERLAP = 0.3

#: Frames of one run embedded, the best two: one is lost to a blink, and past two a short run's
#: frames are the same picture.
FRAMES_PER_TRACK = 2

#: References one agreed appearance may add. One: a long appearance carries a frame from each run,
#: and blended into the person's description they would describe that one video rather than the
#: person.
REFERENCES_PER_APPEARANCE = 1

# --- Detection ----------------------------------------------------------------------------------

#: The square the detector reads. Fixed by the model.
DETECTOR_INPUT = 640

#: How sure the detector must be that something is a face.
DETECTOR_CONFIDENCE = 0.5

#: Overlap above which two detections are the same face found twice.
DETECTOR_OVERLAP = 0.4

#: The strict preset's size floor: the recognizer's own 112-pixel input, below which a face is
#: stretched and its detail is detail nothing measured.
MIN_PIXELS = 112

#: The balanced and lenient presets' size floor: a stretch of 1.17 onto the square, which a
#: recognizer still reads well; nothing lower was measured as good enough.
MIN_PIXELS_ACCEPTED = 96

#: Bumped when HOW a face is judged changes while the numbers in a scan's staleness tag stay put
#: (the side a floor is read off, what containment covers, whose pixels a face is cut from), so
#: every file is offered again under the new measure.
QUALITY_VERSION = 7

#: How close a video's biggest refused face must come to the size floor, as a share of it, for the
#: video to be read again around that moment. Lower, and the look again runs on crowds and stages
#: that no nearby moment brings over the floor.
LOOK_AGAIN_REACH = 0.8

#: Moments added on each side of the nearest face's moment, at most twice this per file: one alone
#: lands halfway, where a face can be under the floor again.
LOOK_AGAIN_EACH_SIDE = 3

#: How long the tuning a sweep started under is kept: a week, far longer than any sweep and short
#: enough that the table stays bounded.
RUN_MEMORY_MS = 7 * 24 * 60 * 60 * 1000

#: How much of the FACE in an aligned square must come from inside the frame (the part
#: `crop._CORE_MARGIN` names; the two are read together). Outside it the edge pixel is repeated as a
#: streak, and a streaked square's description lands near other streaked squares, pulling strangers
#: together. The cut sits at the bottom of a wide empty gap between wrecked squares and clean ones.
MIN_CONTAINMENT = 0.95

#: A blank border, as a share of the picture, added before looking again when the first look found
#: nothing: these detectors miss a face that fills the frame, and on a bigger canvas it is a normal
#: size.
RETRY_BORDER = 0.25

#: The long side a frame is reduced to before detection: the detector reads a 640 square, so more is
#: decoded and thrown away. Memory per scan scales with it, times `scans_at_once`.
FRAME_LONG_SIDE = 1280

#: How far round a face, as a multiple of its long side, the piece read back at the file's own size
#: reaches. Faces are cut and measured in the file's pixels, not the reduced frame's, or a large
#: file's faces all fall under the size floor; 1.5 holds everything `Detector.refine` and the
#: alignment reach for, so the square matches one cut from the whole picture (the test beside
#: `windows`).
SOURCE_REACH = 1.5

# --- References -------------------------------------------------------------------------------

#: Distinct pictures of a person (not stored faces, which repeat one moment) below which matching is
#: reported as weak: five, where recognition stops being a coin toss on this recognizer's curve.
MIN_REFERENCES = 5

#: Where recognition becomes dependable rather than working. It decides both what a person's screen
#: says and the bar in `bar_for`, because a gallery that describes somebody well is what makes both
#: honest.
STRONG_REFERENCES = 10

#: The number of distinct pictures worth aiming at, which a person's strength is shown against: past
#: twenty the accuracy curve is flat. A target, never a rule; it shows why somebody is not being
#: recognized.
GOOD_REFERENCES = 20

#: Faces people confirmed as somebody (`Origin.CONFIRMED`) before a group question that clears her
#: line is named like any match: a description from fewer is too thin for a group nobody looked at.
GROUP_NAMING_REFERENCES = GOOD_REFERENCES

#: Faces people confirmed as somebody before Sift learns from its own names of her: under it a
#: wrong name would teach the very description that made it.
LEARNING_REFERENCES = GOOD_REFERENCES

#: How sure a name Sift added must be before the face becomes one of her references: the ordinary
#: attach line, never the relaxed one, because every later face is compared with a reference.
LEARNING_CONFIDENCE = AUTO_APPLY_CONFIDENCE

#: How good an agreed face must be to be filed as a reference too. Naming a face and learning from
#: it are two decisions: the name is applied regardless, but a crop of a phone held in front of
#: somebody would pull their averaged description toward a phone. Set at the bottom fifth of face
#: quality as a library measures it.
REFERENCE_QUALITY = 0.22

#: Two references this alike add nothing and slow matching: nearly the same picture, well above the
#: same person.
NEAR_DUPLICATE = 0.92

#: A reference this far below the average likeness to that person's others: almost always somebody
#: else's face, invisible to every other check because the picture is good.
ODD_ONE_OUT = 0.32

#: How alike a new face must be to one already decided about in the same file for the decision
#: (removed, or set aside) to apply again. Far above the person bar: it recognizes the same thing in
#: the same file, and at the person bar it would take a real person's face down with it.
ALREADY_DECIDED = 0.92

#: How far back one person's own screens look, in their appearances: visibility is settled over the
#: whole set before a page is taken, so the set needs a bound, and this one is far past a sitting's
#: review.
PERSON_FACES_AT_MOST = 5000

#: Proposed appearances the look-alike card gathers over, library-wide. Its own number because that
#: set must be read whole before it is gathered by person; past it the card says no more than it
#: read.
SUGGESTIONS_AT_MOST = 5000

#: Files asked about for "the one face here is not the person filed on it", library-wide: its own
#: population, bounded the same way, and past it the list says only what it read.
FILED_FACES_AT_MOST = 5000

#: The smallest unnamed group listed, in faces the viewer may see: small groups are most of a
#: library and bury the ones worth a question. Those under it are counted in one line that opens
#: them, and the floor is applied where groups are counted so the pager and the page agree.
STRANGER_FLOOR = 5

# --- A whole group against a person ---------------------------------------------------------------
#
# These compare a GROUP (the middle of a pile of alike faces) with a person, and only ever ask: a
# whole group is never attached, whatever it scores. A group's middle is an average, so it is read
# on its own measured scale, not the single-face lines above. A wrong question costs one press and
# a missed one leaves a group unnamed for good, so the lines sit low in the gap between groups that
# were the person and groups that were somebody else.

#: The closest a group must come to a person to be asked about at all: above every group measured as
#: somebody else, well below the lowest measured as the person. Shown unticked up to `GROUP_TICK`.
GROUP_ASK = 0.35

#: At or above this a group starts ticked on the card: one open group in twenty. A group proposed
#: for a reason outside the faces (her folder) is ticked whatever it scores.
GROUP_TICK = 0.40

#: The fewest unnamed faces before a group is compared with anybody: under three its middle is the
#: single-face scale the ordinary match reads.
GROUP_AT_LEAST = 3

#: A group is not offered as somebody when more than this share of its faces were refused as them:
#: refusals are kept per face, so a No outlives a regrouping.
GROUP_REFUSED_SHARE = 0.5

#: How many groups one person's card lists, closest first. The rest come up as these are answered.
GROUPS_PER_CARD = 12

#: Faces of each group a card shows: the ones a Yes confirms because somebody saw them; the rest are
#: offered as questions (`FaceService._offer_their_groups`).
FACES_PER_GROUP = 6

# --- Storage ------------------------------------------------------------------------------------

#: The widest a cover is cut: the recognizer's square is cropped to the features and too small for a
#: wall, so a cover is cut again from its frame, once, when it is set.
COVER_SIZE = 512

#: How much is kept round the face box in a cover, as a share of the box: the box alone reads as a
#: head cut off.
COVER_MARGIN = 0.45

#: The long side a frame is decoded to for a cover: a person looks at a cover, so here the pixels
#: are the point. One frame, once.
COVER_LONG_SIDE = 1920

#: Crops are JPEG at this quality, not PNG: a fifth of the disk in the backed-up directory, for a
#: shift in a face's description far inside the gap between people.
CROP_QUALITY = 95
