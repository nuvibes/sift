# SPDX-License-Identifier: AGPL-3.0-or-later
"""Recognizing faces: finding them, grouping the ones that look alike, and naming them.

Sift can tell you a file exists. This is what lets it tell you who is in it and, more usefully,
group the hundreds of faces belonging to people you have not named yet, so that naming somebody is
one question instead of hundreds.

**It is off until somebody turns it on, and off means off.** No model is loaded, nothing is
downloaded, nothing is written, and the recognition runtime is not even imported. That is a consent
gate rather than a tidy default: what this feature measures is people's faces, everything it
measures stays on this machine, and none of it ever crosses the network.

**Sift ships no models.** The most accurate ones available are published for non-commercial
research only, so putting them inside a public image would be handing them on under terms nobody
granted. Enabling the feature offers to fetch a pair, or to use copies already on the machine,
which is also the answer for a machine with no route to the internet.

Two things about the design are worth knowing from outside it.

**The unit is a face, not a file.** A photograph of three people is three faces, in three groups,
attributed to three people independently. Saying "not this person" about one of them leaves the
other two exactly as they were.

**Every face's description is kept.** That is what makes adding a person cost seconds rather than a
second pass over the whole library: the comparison is against numbers already held, and nothing is
opened. It is also what makes changing models cheap, because the cropped faces are kept too.
"""

from __future__ import annotations

from sift.kernel.jobs.quiet_hours import WHEN_WORK
from sift.kernel.jobs.schedules import PER_FILE, ScheduledTask, register_schedule
from sift.slices.faces import forgetting, schema, settings, tidy
from sift.slices.faces.evidence import FaceEvidence
from sift.slices.faces.jobs import (
    FACE_BOX_QUESTIONS,
    FACE_FETCH_WEIGHTS,
    FACE_FLOOR,
    FACE_PEOPLE_FROM_FILES,
    FACE_REGROUP,
    FACE_REMATCH,
    FACE_REMEASURE,
    FACE_SCAN,
    FACE_WHOLE_PICTURE,
    AskedOnlyRecords,
    StarterRecords,
    register_handlers,
)
from sift.slices.faces.jobs import (
    scan as scan_file,
)
from sift.slices.faces.jobs_agree import register_agreeing
from sift.slices.faces.models import (
    Attribution,
    Depth,
    Finding,
    Origin,
    PileStatus,
    ScanStatus,
    ToCheckKind,
    ToCheckShow,
)
from sift.slices.faces.packs import Pack, PackError
from sift.slices.faces.queue import (
    DISAGREEMENTS,
    IDENTIFIED,
    IGNORED,
    PEOPLE_KNOWN,
    SET_ASIDE,
    SUGGESTIONS,
    TO_NAME,
    DisagreementsQueue,
    IdentifiedRecords,
    IgnoredRecords,
    PeopleKnownQueue,
    SetAsideQueue,
    SuggestionsQueue,
    ToNameQueue,
)
from sift.slices.faces.references import PersonReport
from sift.slices.faces.router import router
from sift.slices.faces.runner import DeviceUnavailable
from sift.slices.faces.service import (
    FACE_STARTERS,
    MODELS_NOT_INSTALLED,
    SERVICE,
    FacesDisabled,
    FaceService,
    Recognition,
    SettingsReader,
    status_of,
)
from sift.slices.faces.service_box import BoxQuestionRecords
from sift.slices.faces.service_fingerprints import FingerprintRecords
from sift.slices.faces.service_learning import RetiredPickRecords
from sift.slices.faces.service_waiting import RemovedFingerprintRecords
from sift.slices.faces.settings import (
    BUDGET_KEY,
    CORE_SHARE_KEY,
    ENABLED_KEY,
    MODEL_KEY,
    PEOPLE_FROM_FILES_KEY,
    THREAD_COUNT_KEY,
    scans_at_once,
)
from sift.slices.faces.store import PRODUCT, Store
from sift.slices.faces.weights import CATALOG, WeightError

settings.register()
tidy.register()

#: Face recognition as a task: looking for faces in new files. Its When decides whether that happens
#: as files arrive, in quiet hours, or only when pressed. Run now is an Identify run over the
#: library for faces alone. As files arrive by default: the feature switch is the consent and starts
#: off, so this answer only counts once somebody turns recognition on, and a When somebody chose is a
#: stored row that a default never moves.
register_schedule(
    ScheduledTask(
        id="faces",
        title="Identify faces",
        explain="Looks for faces in new files and matches them to your People.",
        job_type=FACE_SCAN,
        needs_starter=True,
        when_default=WHEN_WORK,
        set_in="faces",
        unit=PER_FILE,
        switch=ENABLED_KEY,
    )
)

__all__ = [
    "BUDGET_KEY",
    "CATALOG",
    "CORE_SHARE_KEY",
    "DISAGREEMENTS",
    "ENABLED_KEY",
    "FACE_BOX_QUESTIONS",
    "FACE_FETCH_WEIGHTS",
    "FACE_FLOOR",
    "FACE_PEOPLE_FROM_FILES",
    "FACE_REGROUP",
    "FACE_REMATCH",
    "FACE_REMEASURE",
    "FACE_SCAN",
    "FACE_STARTERS",
    "FACE_WHOLE_PICTURE",
    "IDENTIFIED",
    "IGNORED",
    "MODELS_NOT_INSTALLED",
    "MODEL_KEY",
    "PEOPLE_FROM_FILES_KEY",
    "PEOPLE_KNOWN",
    "PRODUCT",
    "SERVICE",
    "SET_ASIDE",
    "SUGGESTIONS",
    "THREAD_COUNT_KEY",
    "TO_NAME",
    "AskedOnlyRecords",
    "Attribution",
    "BoxQuestionRecords",
    "Depth",
    "DeviceUnavailable",
    "DisagreementsQueue",
    "FaceEvidence",
    "FaceService",
    "FacesDisabled",
    "Finding",
    "FingerprintRecords",
    "IdentifiedRecords",
    "IgnoredRecords",
    "Origin",
    "Pack",
    "PackError",
    "PeopleKnownQueue",
    "PersonReport",
    "PileStatus",
    "Recognition",
    "RemovedFingerprintRecords",
    "RetiredPickRecords",
    "ScanStatus",
    "SetAsideQueue",
    "SettingsReader",
    "StarterRecords",
    "Store",
    "SuggestionsQueue",
    "ToCheckKind",
    "ToCheckShow",
    "ToNameQueue",
    "WeightError",
    "forgetting",
    "register_agreeing",
    "register_handlers",
    "router",
    "scan_file",
    "scans_at_once",
    "schema",
    "settings",
    "status_of",
    "tidy",
]
