---
title: For developers
description: Build Sift from source and read its API.
---

Sift's source is on GitHub, under the AGPL-3.0-or-later license, with the steps to build it in CONTRIBUTING.md. To start, read [CONTRIBUTING.md](https://github.com/nuvibes/sift/blob/main/CONTRIBUTING.md#build-and-run-sift).

## Build Sift from source

You need Python 3.13, [uv](https://docs.astral.sh/uv/) and Node.js 22 (the exact version is in `.nvmrc`). From a clone of the repository:

```sh
uv sync --frozen --extra dev                 # the exact versions in uv.lock, with the checks
uv run pre-commit install --install-hooks    # the commit, commit-message and pre-push hooks
(cd frontend && npm ci && npm run build)     # the browser client, built into src/sift/web
uv run sift                                  # Sift, on http://127.0.0.1:5171
```

On Windows, `uv run python scripts/fetch_vendor.py` downloads ffmpeg, the libwebp tools, yt-dlp, gallery-dl and QuickJS, and checks each against a pinned SHA-256. It also checks the two things the repository builds itself, the tunnel client and the HEIF reader's wheel, and `--build-missing` builds one that isn't there. Elsewhere Sift uses the copies on your `PATH`. The desktop app is in `desktop/`, and `scripts/release.py` builds the installer.

[CONTRIBUTING.md](https://github.com/nuvibes/sift/blob/main/CONTRIBUTING.md#the-checks) also lists the checks every change runs and how to write a commit. A change written with a language model is welcome after a person has reviewed it.

## How the code is put together

[ARCHITECTURE.md](https://github.com/nuvibes/sift/blob/main/ARCHITECTURE.md) sets out how the code is organized and which check holds which rule. It's the one copy, kept in the source tree beside the code it describes.

## The API

Every route Sift answers is listed in the [API reference](/developers/api/#_top), written from the OpenAPI file Sift publishes. The routes serve Sift's own browser client and desktop app. They aren't a public API, and they change between releases without notice.
