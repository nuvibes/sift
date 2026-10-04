// Records that a build of the client finished. The last step of `npm run build`.
//
// It exists so that `build_if_stale.js` has something honest to compare against. The output folder
// cannot answer "when did a build last finish": `vite build` copies `frontend/static` in with the
// original modification times, so the oldest file in a brand new client is as old as the oldest
// file in `static`, which would make the freshness check always say stale and rebuild.
//
// Chained with `&&` in the build script, so it runs only when the build succeeded and only after
// everything the build writes has been written. An interrupted build leaves the previous marker,
// which is older than whatever it managed to write into the output, and the check refuses that.

import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { markerPath } from './lib/build-freshness.js';

const marker = markerPath(join(dirname(fileURLToPath(import.meta.url)), '..'));
await mkdir(dirname(marker), { recursive: true });
// The time is the file's own; the contents are for whoever finds it and wonders what it is.
await writeFile(marker, 'Written when a build of the client finished. Its time is the answer.\n');
