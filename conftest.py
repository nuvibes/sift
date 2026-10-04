# SPDX-License-Identifier: AGPL-3.0-or-later
"""Registers the shared fixtures for the whole test session.

Slice tests live next to the slice they cover (`kernel/tests/`, `slices/<name>/tests/`), which
puts them out of reach of a conftest further up the tree. Registering it as a plugin here makes
one set of fixtures available everywhere, so a slice never has to grow its own copy.

The fixtures live in `sift.testing`, not `sift.tests`: it holds no tests, only the helpers the
tests are built from. A package named `tests` that contains none is a trap for the next reader.
"""

pytest_plugins = ["sift.testing.fixtures", "sift.testing.bytes_compared"]
