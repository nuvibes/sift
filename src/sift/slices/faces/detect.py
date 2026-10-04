# SPDX-License-Identifier: AGPL-3.0-or-later
"""Finding faces in a frame, and where their eyes, nose and mouth are.

A detector answers two questions at once and the second one matters more than it looks. The box
says a face is here; the five points say *how it is arranged*, and everything downstream is
aligned by those five points, so a detector with slightly worse points produces measurably worse
recognition even when it finds exactly the same faces. That is not a theory: swapping only the
detector, holding the recognizer fixed, moved the share of appearances correctly identified by
several points.

Two families are supported and they decode their answers differently, which is why there are two
classes here rather than one with a flag. What they have in common (squaring the frame up for the
model, discarding the same face found twice, and looking again with a border when nothing was found)
lives outside both of them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from sift.slices.faces import tuning
from sift.slices.faces.models import Box, Detection
from sift.slices.faces.runner import Loaded, RunnerLike

#: The distances between successive positions the model checks, in pixels of the input. Three
#: scales, so a face is found whether it fills the frame or is one of a crowd.
_STRIDES = (8, 16, 32)


def letterbox(frame: np.ndarray, size: int) -> tuple[np.ndarray, float]:
    """Fit a frame into a square of `size`, keeping its shape, padding the rest with black.

    Squashing it to a square instead would be simpler and would distort every face by the frame's
    aspect ratio, which for a phone video shot upright is a face half as wide as it should be.
    """
    height, width = frame.shape[:2]
    scale = min(size / height, size / width)
    new_height = max(1, round(height * scale))
    new_width = max(1, round(width * scale))
    canvas = np.zeros((size, size, 3), dtype=np.uint8)
    canvas[:new_height, :new_width] = _resize(frame, new_width, new_height)
    return canvas, scale


def _resize(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    """Nearest-neighbour resize.

    Cruder than an averaged shrink, and the difference does not survive what happens next: the
    picture is about to be reduced to a 640-pixel square and read by a model that was trained on
    exactly this kind of resized input. The averaged version costs several times as much per frame
    on a path that runs for every sampled moment of every file.
    """
    source_height, source_width = frame.shape[:2]
    rows = (np.arange(height) * (source_height / height)).astype(np.int64)
    columns = (np.arange(width) * (source_width / width)).astype(np.int64)
    picked = frame[np.clip(rows, 0, source_height - 1)][:, np.clip(columns, 0, source_width - 1)]
    return np.asarray(picked, dtype=np.uint8)


def add_border(frame: np.ndarray, fraction: float) -> tuple[np.ndarray, int, int]:
    """Put a black border round a frame, and say how far the picture moved.

    For the face that fills the whole frame. These detectors are trained on faces occupying a
    modest part of a scene, and a close-up crop (the top of the head and the chin both outside
    the picture) is outside anything they have been shown, so they find nothing at all. On a
    bigger canvas the same face is an ordinary size and is found immediately.
    """
    height, width = frame.shape[:2]
    pad_y = int(height * fraction)
    pad_x = int(width * fraction)
    bordered = np.zeros((height + 2 * pad_y, width + 2 * pad_x, 3), dtype=np.uint8)
    bordered[pad_y : pad_y + height, pad_x : pad_x + width] = frame
    return bordered, pad_x, pad_y


def suppress(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
    """Keep the best of each cluster of overlapping boxes.

    A detector reports the same face from several nearby positions and scales. Without this, one
    face becomes six detections, six crops and six embeddings.
    """
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        best = int(order[0])
        keep.append(best)
        rest = order[1:]
        if rest.size == 0:
            break
        left = np.maximum(x1[best], x1[rest])
        top = np.maximum(y1[best], y1[rest])
        right = np.minimum(x2[best], x2[rest])
        bottom = np.minimum(y2[best], y2[rest])
        overlap_area = np.maximum(0.0, right - left) * np.maximum(0.0, bottom - top)
        union = areas[best] + areas[rest] - overlap_area
        order = rest[(overlap_area / np.maximum(union, 1e-9)) <= threshold]
    return keep


class Detector(ABC):
    """What every detector family offers: faces in a frame, in that frame's own pixels."""

    def __init__(
        self, runner: RunnerLike, loaded: Loaded, *, size: int = tuning.DETECTOR_INPUT
    ) -> None:
        self._runner = runner
        self._loaded = loaded
        self._size = size
        self._positions: dict[tuple[int, int], np.ndarray] = {}

    @abstractmethod
    def _blob(self, canvas: np.ndarray) -> np.ndarray:
        """Turn the squared-up frame into what this model expects to be handed."""

    @abstractmethod
    def _decode(
        self, outputs: list[np.ndarray], scale: float, threshold: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Read the model's answer as boxes, landmarks and scores, in the frame's own pixels."""

    def _grid(self, stride: int, anchors: int) -> np.ndarray:
        """The positions this model checks at one scale, cached because they never change."""
        key = (self._size, stride)
        if key not in self._positions:
            side = self._size // stride
            rows, columns = np.mgrid[:side, :side]
            points = np.stack([columns.ravel(), rows.ravel()], axis=-1).astype(np.float32)
            if anchors > 1:
                points = np.repeat(points, anchors, axis=0)
            self._positions[key] = points
        return self._positions[key]

    def _run(self, frame: np.ndarray, threshold: float, timestamp_ms: int) -> list[Detection]:
        canvas, scale = letterbox(frame, self._size)
        outputs = self._runner.run(self._loaded, self._blob(canvas))
        boxes, landmarks, scores = self._decode(outputs, scale, threshold)
        if len(boxes) == 0:
            return []
        keep = suppress(boxes, scores, tuning.DETECTOR_OVERLAP)
        found = []
        for index in keep:
            x1, y1, x2, y2 = boxes[index]
            found.append(
                Detection(
                    box=Box(
                        x=round(float(x1)),
                        y=round(float(y1)),
                        width=max(1, round(float(x2 - x1))),
                        height=max(1, round(float(y2 - y1))),
                    ),
                    score=float(scores[index]),
                    landmarks=tuple((float(px), float(py)) for px, py in landmarks[index]),
                    timestamp_ms=timestamp_ms,
                )
            )
        return found

    def detect(
        self,
        frame: np.ndarray,
        *,
        timestamp_ms: int = 0,
        threshold: float = tuning.DETECTOR_CONFIDENCE,
        retry: bool = True,
    ) -> list[Detection]:
        """Every face in a frame, with the second look when the first found nothing.

        **Kept on a measurement.** The border costs nothing on a frame where the first look
        succeeded; what it costs is a second forward pass on every frame that found nothing. On a
        sample of frames, each fitted into the square exactly as the pass fits one, the first look
        found nothing on about a third, so the border makes 1.36 detector passes a frame rather
        than 1.00, and it then finds a face on about one empty frame in twenty, 2.4 percent more
        faces than the first look finds on its own.

        So it is a third more of the cheapest step of the pass for one face in forty. It stays,
        and the reason is the asymmetry rather than the ratio: a detector pass is about 2.4 ms on
        a card and 3 ms on the processor against a decode that costs tens of times that, while a
        face nobody found is not found again: the file is recorded as looked at, and only a
        rescan would ever revisit it.
        """
        found = self._run(frame, threshold, timestamp_ms)
        if found or not retry or tuning.RETRY_BORDER <= 0:
            return found

        bordered, pad_x, pad_y = add_border(frame, tuning.RETRY_BORDER)
        found = self._run(bordered, threshold, timestamp_ms)
        return [_shift(item, -pad_x, -pad_y) for item in found]

    def refine(self, frame: np.ndarray, detection: Detection, *, margin: float = 0.8) -> Detection:
        """Look again at just this face, filling the input, for landmarks worth aligning by.

        A face occupying forty pixels of a 4K frame is eight pixels across once the frame has been
        reduced to the square the model reads, and its five points are pinned to that eight-pixel
        grid. Alignment inherits every bit of that error, and alignment is what recognition is most
        sensitive to. Looking again at a crop around the face puts the points back where they
        belong for one more small forward pass, so it is done only for the faces about to be
        recognized, never for every detection.
        """
        box = detection.box
        centre_x, centre_y = box.centre()
        half = max(box.width, box.height) * (1 + margin) / 2
        left = int(max(0, centre_x - half))
        top = int(max(0, centre_y - half))
        right = int(min(frame.shape[1], centre_x + half))
        bottom = int(min(frame.shape[0], centre_y + half))
        if right - left < 24 or bottom - top < 24:
            return detection

        window = frame[top:bottom, left:right]
        found = self._run(window, tuning.DETECTOR_CONFIDENCE, detection.timestamp_ms)
        if not found:
            return detection
        best = max(found, key=lambda item: item.box.area)
        moved = _shift(best, left, top)
        # The original score is kept: the second look is about where the features are, not about
        # whether this is a face, and it is looking at a picture chosen to contain one.
        return Detection(
            box=moved.box,
            score=detection.score,
            landmarks=moved.landmarks,
            timestamp_ms=detection.timestamp_ms,
        )


def _shift(detection: Detection, dx: int, dy: int) -> Detection:
    box = detection.box
    return Detection(
        box=Box(x=box.x + dx, y=box.y + dy, width=box.width, height=box.height),
        score=detection.score,
        landmarks=tuple((x + dx, y + dy) for x, y in detection.landmarks),
        timestamp_ms=detection.timestamp_ms,
    )


class AnchorDetector(Detector):
    """The accurate family. Reports, at each position, how far the box's four edges are from it."""

    _ANCHORS = 2

    def _blob(self, canvas: np.ndarray) -> np.ndarray:
        return ((canvas.astype(np.float32) - 127.5) / 128.0).transpose(2, 0, 1)[None].copy()

    def _decode(
        self, outputs: list[np.ndarray], scale: float, threshold: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        levels = len(_STRIDES)
        boxes: list[np.ndarray] = []
        points: list[np.ndarray] = []
        scores: list[np.ndarray] = []
        for index, stride in enumerate(_STRIDES):
            score = outputs[index].reshape(-1)
            chosen = np.nonzero(score >= threshold)[0]
            if chosen.size == 0:
                continue
            edges = outputs[index + levels].reshape(-1, 4)[chosen] * stride
            marks = outputs[index + levels * 2].reshape(-1, 5, 2)[chosen] * stride
            centres = self._grid(stride, self._ANCHORS)[chosen] * stride
            boxes.append(
                np.stack(
                    [
                        centres[:, 0] - edges[:, 0],
                        centres[:, 1] - edges[:, 1],
                        centres[:, 0] + edges[:, 2],
                        centres[:, 1] + edges[:, 3],
                    ],
                    axis=-1,
                )
            )
            points.append(centres[:, None, :] + marks)
            scores.append(score[chosen])
        return _gather(boxes, points, scores, scale)


class PyramidDetector(Detector):
    """The permissive family. Reports a centre and a size, and names its outputs rather than
    relying on their order."""

    _ANCHORS = 1

    def _blob(self, canvas: np.ndarray) -> np.ndarray:
        # This family was trained on frames in the order a certain imaging library hands them over,
        # which is the reverse of Sift's, and on raw values rather than centred ones.
        return canvas[:, :, ::-1].astype(np.float32).transpose(2, 0, 1)[None].copy()

    def _decode(
        self, outputs: list[np.ndarray], scale: float, threshold: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        named = dict(zip(self._loaded.outputs, outputs, strict=True))
        boxes: list[np.ndarray] = []
        points: list[np.ndarray] = []
        scores: list[np.ndarray] = []
        for stride in _STRIDES:
            # Two separate opinions (is there an object here, and is that object a face), which
            # the publisher's own reader combines as a geometric mean.
            objectness = named[f"obj_{stride}"].reshape(-1)
            face = named[f"cls_{stride}"].reshape(-1)
            score = np.sqrt(np.clip(objectness, 0.0, 1.0) * np.clip(face, 0.0, 1.0))
            chosen = np.nonzero(score >= threshold)[0]
            if chosen.size == 0:
                continue
            shape = named[f"bbox_{stride}"].reshape(-1, 4)[chosen]
            marks = named[f"kps_{stride}"].reshape(-1, 5, 2)[chosen]
            centres = self._grid(stride, self._ANCHORS)[chosen]
            centre_x = (centres[:, 0] + shape[:, 0]) * stride
            centre_y = (centres[:, 1] + shape[:, 1]) * stride
            width = np.exp(shape[:, 2]) * stride
            height = np.exp(shape[:, 3]) * stride
            boxes.append(
                np.stack(
                    [
                        centre_x - width / 2,
                        centre_y - height / 2,
                        centre_x + width / 2,
                        centre_y + height / 2,
                    ],
                    axis=-1,
                )
            )
            points.append((centres[:, None, :] + marks) * stride)
            scores.append(score[chosen])
        return _gather(boxes, points, scores, scale)


def _gather(
    boxes: list[np.ndarray], points: list[np.ndarray], scores: list[np.ndarray], scale: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Put the three scales together and undo the fitting-into-a-square."""
    if not boxes:
        empty = np.zeros((0, 4), dtype=np.float32)
        return empty, np.zeros((0, 5, 2), dtype=np.float32), np.zeros((0,), dtype=np.float32)
    return (
        np.concatenate(boxes) / scale,
        np.concatenate(points) / scale,
        np.concatenate(scores),
    )


#: Which class reads which family's answers.
FAMILIES: dict[str, type[Detector]] = {
    "accurate": AnchorDetector,
    "permissive": PyramidDetector,
}


def build(runner: RunnerLike, loaded: Loaded) -> Detector:
    """The reader for whichever detector was loaded."""
    try:
        family = FAMILIES[loaded.weight.family]
    except KeyError:
        raise ValueError(f"no detector reader for {loaded.weight.family!r}") from None
    return family(runner, loaded)
