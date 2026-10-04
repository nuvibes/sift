#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prove the face runtime works as it ships, and that no model ships with it.

The arithmetic here (resampling a face into the square a model expects, measuring whether it is
worth recognising, comparing two descriptions) is all numpy and the inference runtime, exactly the
kind of dependency whose behaviour at the edges moves between versions. Run it with the installer's
own runtime, or in a checkout's environment, which is built from the same lock.

The other half is a licence check with teeth: **no model file may ship.** The most accurate face
models available are published for non-commercial research only, so one inside the installer would
be redistribution nobody granted. A grep over the source is not enough, so this walks the trees that
ship: the environment running it, and every folder named on the command line (an unpacked installer,
say).

Usage: check_face_runtime.py [folder ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

FAILURES: list[str] = []

#: What a model file looks like. Deliberately broad: the point is that nothing of the sort is here,
#: not that one particular naming convention is absent. `.pth` is left out because it is also what
#: Python calls a path-configuration file, and every virtual environment has several.
MODEL_SUFFIXES = (".onnx", ".pt", ".safetensors", ".tflite", ".caffemodel")

#: The inference runtime ships three tiny graphs of its own (a sigmoid, a multiply, and a
#: logistic regression over a flower dataset) as examples inside its own package. They are the
#: runtime's, they are covered by its own licence, and they are not face models. Named exactly,
#: rather than loosening the rule, so a real model appearing anywhere still fails.
RUNTIME_EXAMPLES = "/site-packages/onnxruntime/datasets/"


def check(condition: bool, message: str) -> None:
    if condition:
        print(f"  ok   {message}")
    else:
        print(f"  FAIL {message}")
        FAILURES.append(message)


def no_models_ship(roots: list[Path]) -> None:
    """The licence check, done by looking rather than by trusting.

    A grep over the source would not see a file added by a dependency or copied in by a build step,
    so this walks every tree that ships. Compared as a POSIX path, so the runtime's own examples are
    recognised on Windows too.
    """
    found: list[str] = []
    for base in roots:
        if not base.is_dir():
            check(False, f"{base} is a folder to walk")
            continue
        for path in base.rglob("*"):
            if path.suffix.lower() not in MODEL_SUFFIXES or not path.is_file():
                continue
            if RUNTIME_EXAMPLES in path.as_posix():
                continue
            found.append(str(path))
    check(not found, f"no model file ships (found: {found[:5]})")


def the_runtime_is_present_and_offers_the_processor() -> None:
    import onnxruntime

    providers = onnxruntime.get_available_providers()
    check("CPUExecutionProvider" in providers, f"the runtime offers the processor: {providers}")

    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = 1
    check(options.intra_op_num_threads == 1, "inference can be held to one thread")


def a_face_can_be_aligned_and_measured() -> None:
    """The arithmetic, on the numpy that ships."""
    import numpy as np

    from sift.slices.faces import quality
    from sift.slices.faces.crop import CHIP_SIZE, LANDMARK_TEMPLATE, align, digest
    from sift.slices.faces.models import Box

    rng = np.random.default_rng(1)
    frame = rng.integers(40, 210, (400, 300, 3), dtype=np.uint8)
    landmarks = tuple((float(x) * 2 + 40.0, float(y) * 2 + 30.0) for x, y in LANDMARK_TEMPLATE)

    # `align` hands back the square AND how much of it was really the picture rather than the grey
    # it was padded with. Both travel together because the quality bar reads the second, and a
    # caller that took the square alone would be measuring a crop without knowing it ran off an edge.
    aligned = align(frame, landmarks)
    chip = aligned.chip
    check(chip.shape == (CHIP_SIZE, CHIP_SIZE, 3), f"a face aligns to {CHIP_SIZE} square")
    check(chip.dtype == np.uint8, "an aligned face is whole pixel values")
    check(
        0.0 <= aligned.containment <= 1.0,
        f"how much of the square is picture is a fraction: {aligned.containment}",
    )

    verdict = quality.assess(
        Box(x=0, y=0, width=150, height=150), landmarks, chip, containment=aligned.containment
    )
    check(verdict.accepted, f"a good face clears the bar (reason: {verdict.reason})")
    check(0.0 < verdict.score <= 1.0, f"the score is a fraction: {verdict.score}")

    poor = quality.assess(
        Box(x=0, y=0, width=8, height=8), landmarks, chip, containment=aligned.containment
    )
    check(not poor.accepted, "a face of eight pixels does not clear the bar")

    check(len(digest(b"a picture")) == 64, "a picture's identity is a full-length digest")


def descriptions_can_be_compared() -> None:
    from sift.slices.faces import clustering, matching, recognize
    from sift.slices.faces.models import Reference

    first = recognize.unpack(recognize.pack((0.6, 0.8, 0.0, 0.0)))
    check(
        abs(recognize.similarity(first, first) - 1.0) < 1e-6,
        "a face compared with itself is a perfect match",
    )

    gallery = matching.build_gallery(
        {
            "ada": [
                Reference(
                    id="a",
                    person_id="ada",
                    vector=(1.0, 0.0, 0.0, 0.0),
                    quality=1.0,
                    crop_digest="a",
                )
            ],
            "grace": [
                Reference(
                    id="b",
                    person_id="grace",
                    vector=(0.0, 1.0, 0.0, 0.0),
                    quality=1.0,
                    crop_digest="b",
                )
            ],
        }
    )
    match = matching.best_match((0.98, 0.2, 0.0, 0.0), gallery)
    check(match is not None and match.person_id == "ada", "a face matches the person it looks like")
    check(
        matching.best_match((0.0, 0.0, 1.0, 0.0), gallery) is None,
        "a face that looks like nobody matches nobody",
    )

    piles = clustering.pile_up([(1.0, 0.0, 0.0, 0.0), (0.99, 0.14, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0)])
    check(len(piles) == 2, f"two different people make two piles, not one: {len(piles)}")


def a_pack_round_trips() -> None:
    from sift.slices.faces import packs

    raw = packs.build(
        name="Conformance",
        version="1",
        recognizer="some-model",
        dimension=4,
        people=[
            packs.PackedPerson(
                name="Ada",
                aliases=("ada l",),
                links=(),
                faces=(
                    packs.PackedFace(
                        digest="a", quality=0.9, vector=(1.0, 0.0, 0.0, 0.0), picture=None
                    ),
                ),
            )
        ],
        include_pictures=False,
    )
    pack = packs.read(raw, expect_recognizer="some-model")
    check(pack.face_count == 1, "a pack survives being written and read back")

    try:
        packs.read(raw, expect_recognizer="a-different-model")
    except packs.PackError:
        check(True, "a pack made by a different model is refused")
    else:
        check(False, "a pack made by a different model is refused")


def the_feature_starts_switched_off() -> None:
    import sift.slices.faces  # noqa: F401 - importing is what declares the switches
    from sift.kernel.settings_registry import get_registered

    declared = get_registered("faces.enabled")
    check(
        declared is not None and declared.default is False, "face recognition starts switched off"
    )


def main() -> int:
    roots = [Path(sys.prefix), *(Path(folder) for folder in sys.argv[1:])]
    print(f"face runtime, as it ships, from {sys.prefix}:")
    no_models_ship(roots)
    the_runtime_is_present_and_offers_the_processor()
    a_face_can_be_aligned_and_measured()
    descriptions_can_be_compared()
    a_pack_round_trips()
    the_feature_starts_switched_off()

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
