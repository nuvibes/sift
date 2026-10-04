// Writes the family-to-face table into `static/theme-boot.js`, from the one file that holds it.
//
// The boot script runs before the module graph exists, so it cannot import the table the store
// reads (`src/lib/theme/carried-faces.json`). Without the table it would stamp an older copy's
// family name (`grotesk`) as it stands, no stylesheet rule matches it, and the first frame after an
// update would be painted in the default faces. A copy kept by hand is the other way to get it
// there, and the one that drifts; so the table is written into the script between two marker
// lines, here, by `npm run fonts` (which `dev`, `build` and `prepare` all run), and
// `src/lib/theme/theme-boot.test.ts` refuses a script whose table and the file disagree.
//
// Formatted with the repository's own prettier settings after writing, so a run that changes
// nothing in the table writes the file back byte for byte.

import { readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as prettier from 'prettier';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const TABLE = join(root, 'src/lib/theme/carried-faces.json');
const BOOT = join(root, 'static/theme-boot.js');

const START = '// BEGIN carried faces: written by scripts/write_theme_boot.js. Edit the json.';
const END = '// END carried faces';

const table = JSON.parse(await readFile(TABLE, 'utf8'));
const carried = { display: table.display, body: table.body };
const source = await readFile(BOOT, 'utf8');
const from = source.indexOf(START);
const to = source.indexOf(END);
if (from < 0 || to < from) {
	console.error(`${BOOT} has no "${START}" ... "${END}" block to write the table into.`);
	process.exit(1);
}
const written =
	source.slice(0, from) +
	`${START}\n\t\tvar carried = ${JSON.stringify(carried)};\n\t\t` +
	source.slice(to);
const options = (await prettier.resolveConfig(BOOT)) ?? {};
const formatted = await prettier.format(written, { ...options, filepath: BOOT });
if (formatted !== source) await writeFile(BOOT, formatted);
