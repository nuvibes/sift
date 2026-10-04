#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fail the build if a dependency's license is incompatible with Sift's.

Sift is AGPL-3.0. A dependency under an incompatible license (a proprietary one, or GPL-2.0
with no "or later" clause, which cannot be combined with GPLv3-family code) would make the whole
distribution unshippable, quietly, the day it was added. This reads the license off every component
in the CycloneDX bill of materials and refuses anything that is not known to be compatible.

It fails closed: a license it does not recognize is a failure, not a pass, because "unrecognized"
and "incompatible" look identical from a distance and only one of them is safe to guess about. A
package whose metadata declares no license at all is handled the same way, unless it is listed in
REVIEWED_EXCEPTIONS with the license read by hand from its own LICENSE file.

Usage: check_licenses.py sbom.cdx.json
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

# SPDX identifiers whose obligations an AGPL-3.0 work can satisfy: permissive licenses, the weak
# copyleft that only binds its own files, and the GPL family from v3 onward (and v2-or-later, which
# can move up to v3). GPL-2.0-only is deliberately absent: it cannot be combined with GPLv3-family
# code, which is the one strong-copyleft trap this check exists to catch.
ALLOWED_SPDX = frozenset(
    {
        # Permissive.
        "MIT",
        "MIT-0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "BSD-3-Clause-Clear",
        "ISC",
        "Apache-2.0",
        "Apache-1.1",
        "Python-2.0",
        "Python-2.0.1",
        "PSF-2.0",
        "HPND",
        # Pillow's: the CMU permission notice, the same family as HPND.
        "MIT-CMU",
        "Zlib",
        "0BSD",
        "Unlicense",
        "CC0-1.0",
        "BlueOak-1.0.0",
        "WTFPL",
        # Weak copyleft (file-scoped; compatible as a dependency).
        "MPL-2.0",
        "LGPL-2.0-or-later",
        "LGPL-2.1-only",
        "LGPL-2.1-or-later",
        "LGPL-3.0-only",
        "LGPL-3.0-or-later",
        # Strong copyleft that AGPL-3.0 can incorporate.
        "GPL-2.0-or-later",
        "GPL-3.0-only",
        "GPL-3.0-or-later",
        "AGPL-3.0-only",
        "AGPL-3.0-or-later",
    }
)

# Named for a clear message. Anything not in ALLOWED_SPDX already fails; these just get told apart
# from "unrecognized" so the report can say which problem it is.
INCOMPATIBLE_SPDX = frozenset(
    {
        "GPL-1.0-only",
        "GPL-1.0-or-later",
        "GPL-2.0-only",
        "GPL-2.0",  # bare/deprecated id: read conservatively as v2-only.
        "GPL-1.0",
        "SSPL-1.0",
        "Elastic-2.0",
        "BUSL-1.1",
    }
)

# Free-text license strings, as they appear in Python "License :: OSI Approved :: ..." classifiers
# rather than as SPDX ids. Matched as lowercased substrings.
ALLOWED_NAME_SUBSTRINGS = (
    "mit license",
    "mit-0",
    "bsd license",
    "bsd-2",
    "bsd-3",
    "3-clause bsd",
    "2-clause bsd",
    "apache software license",
    "apache-2",
    "apache 2",
    "mozilla public license",
    "mpl-2",
    "mpl 2",
    "isc license",
    "python software foundation",
    "psf",
    "the unlicense",
    "unlicense",
    "public domain",
    "cc0",
    "zlib",
    "gnu lesser general public license",  # LGPL family: compatible as a dependency.
    "lgpl",
    "gnu library or lesser general public license",
    "gnu general public license v3",
    "gplv3",
    "gnu affero",
    "agpl",
)

# Substrings that mark a string as incompatible or non-free, for a precise message.
INCOMPATIBLE_NAME_SUBSTRINGS = (
    "server side public license",
    "sspl",
    "commons clause",
    "business source",
    "proprietary",
)

# Packages that declare no license in their metadata but whose LICENSE file was read by hand. Each
# is a permissive license verified from the package's own text.
REVIEWED_EXCEPTIONS = {
    # onnxruntime's, for the faces model.
    "protobuf": "BSD-3-Clause, read from the bundled LICENSE and metadata.",
    # aiohttp's transitive deps. multidict declares its licence via a PEP 639 expression the SBOM
    # generator does not surface, so it lands here as "undeclared"; its own metadata License field
    # reads "Apache License 2.0" (read by hand). Apache-2.0 is AGPL-3.0 compatible.
    "multidict": "Apache-2.0, read from the package metadata License field.",
    # The vector add-on. Its metadata License field reads "MIT License, Apache License, Version
    # 2.0", dual-licensed, which the SBOM generator cannot turn into an SPDX identifier, so it
    # lands here as undeclared. Confirmed against the project's own repository, which carries
    # LICENSE-APACHE (Apache-2.0) beside LICENSE-MIT. Either is AGPL-3.0 compatible.
    "sqlite-vec": "MIT or Apache-2.0, read from the package metadata and the project's own "
    "LICENSE-APACHE and LICENSE-MIT files.",
}

ALLOWED = "allowed"
INCOMPATIBLE = "incompatible"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Finding:
    name: str
    version: str
    license: str
    verdict: str


def _verdict_for_token(token: str) -> str:
    """Classify one license identifier or free-text name."""
    token = token.strip().strip("()").strip()
    if not token:
        return UNKNOWN
    if token in ALLOWED_SPDX:
        return ALLOWED
    if token in INCOMPATIBLE_SPDX:
        return INCOMPATIBLE
    low = token.lower()
    if any(bad in low for bad in INCOMPATIBLE_NAME_SUBSTRINGS):
        return INCOMPATIBLE
    if any(good in low for good in ALLOWED_NAME_SUBSTRINGS):
        return ALLOWED
    return UNKNOWN


def _verdict_for_expression(expression: str) -> str:
    """Classify an SPDX expression. OR is satisfied by any compatible operand; AND needs all."""
    expr = expression.strip().strip("()").strip()
    if " AND " in expr:
        parts = [_verdict_for_token(p) for p in expr.split(" AND ")]
        if all(p == ALLOWED for p in parts):
            return ALLOWED
        return INCOMPATIBLE if INCOMPATIBLE in parts else UNKNOWN
    if " OR " in expr:
        parts = [_verdict_for_token(p) for p in expr.split(" OR ")]
        if ALLOWED in parts:
            return ALLOWED
        return INCOMPATIBLE if INCOMPATIBLE in parts else UNKNOWN
    return _verdict_for_token(expr)


def _license_strings(component: dict[str, Any]) -> list[str]:
    """The license identifiers declared on a component, as displayable strings."""
    strings: list[str] = []
    for entry in component.get("licenses", []):
        if "expression" in entry:
            strings.append(str(entry["expression"]))
        elif "license" in entry:
            lic = entry["license"]
            strings.append(str(lic.get("id") or lic.get("name") or "").strip())
    return [s for s in strings if s]


def _component_verdict(component: dict[str, Any]) -> tuple[str, str]:
    """Classify a component. Multiple separate license entries are read as a choice (OR): a
    package offered under several licenses can be used under any one of them."""
    declared = _license_strings(component)
    if not declared:
        name = str(component.get("name", "")).lower()
        if name in REVIEWED_EXCEPTIONS:
            return ALLOWED, f"undeclared; {REVIEWED_EXCEPTIONS[name]}"
        return UNKNOWN, "no license declared"

    verdicts = [_verdict_for_expression(s) for s in declared]
    joined = "; ".join(declared)
    if ALLOWED in verdicts:
        return ALLOWED, joined
    return (INCOMPATIBLE if INCOMPATIBLE in verdicts else UNKNOWN), joined


def scan(sbom: dict[str, Any]) -> list[Finding]:
    """Every component whose license is not known to be compatible with AGPL-3.0."""
    findings: list[Finding] = []
    for component in sbom.get("components", []):
        verdict, detail = _component_verdict(component)
        if verdict != ALLOWED:
            findings.append(
                Finding(
                    name=str(component.get("name", "?")),
                    version=str(component.get("version", "?")),
                    license=detail,
                    verdict=verdict,
                )
            )
    return findings


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_licenses.py sbom.cdx.json", file=sys.stderr)
        return 2

    with open(argv[1]) as handle:
        sbom = json.load(handle)

    total = len(sbom.get("components", []))
    findings = scan(sbom)

    if not findings:
        print(f"licenses: {total} components, all compatible with AGPL-3.0")
        return 0

    print(
        f"licenses: {len(findings)} of {total} components are not known-compatible with AGPL-3.0:"
    )
    for f in sorted(findings, key=lambda f: (f.verdict, f.name)):
        print(f"  {f.verdict:12} {f.name} {f.version}: {f.license}")
    print(
        "\nAn incompatible license cannot ship in an AGPL-3.0 distribution. An unrecognized one has "
        "to be read by hand: if it is compatible, add its identifier to ALLOWED_SPDX, or the package "
        "to REVIEWED_EXCEPTIONS with the license you verified."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
