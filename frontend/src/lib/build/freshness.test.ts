/**
 * Whether the client on disk was built from the source on disk (`scripts/build_if_stale.js`): an
 * oldest-file rule never skips, since `vite build` copies `static/` with its old times.
 */

import { mkdirSync, mkdtempSync, rmSync, utimesSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { markerPath, reasonToBuild } from '../../../scripts/lib/build-freshness.js';

let root: string;
let output: string;
let source: string;
let marker: string;

function at(when: number, ...paths: string[]): void {
	for (const path of paths) utimesSync(path, when, when);
}

const OLD = 1_600_000_000;
const RECENT = 1_700_000_000;

function ask() {
	return reasonToBuild({ output, marker, inputs: [source] });
}

beforeEach(() => {
	root = mkdtempSync(join(tmpdir(), 'sift-freshness-'));
	output = join(root, 'web');
	source = join(root, 'src');
	marker = markerPath(root);
	mkdirSync(output, { recursive: true });
	mkdirSync(source, { recursive: true });
	mkdirSync(join(root, '.svelte-kit'), { recursive: true });
});

afterEach(() => {
	rmSync(root, { recursive: true, force: true });
});

describe('when the built client is current', () => {
	it('**skips the build when nothing has been touched since it finished**', () => {
		writeFileSync(join(source, 'app.ts'), 'x');
		writeFileSync(join(output, 'index.html'), 'x');
		writeFileSync(marker, 'x');
		at(RECENT, join(source, 'app.ts'), join(output, 'index.html'), marker);

		return expect(ask()).resolves.toBeNull();
	});

	it('**skips it even when the output holds a file older than the source**', () => {
		/* The whole fault: a copied `robots.txt` predates every source file. */
		writeFileSync(join(source, 'app.ts'), 'x');
		writeFileSync(join(output, 'robots.txt'), 'x');
		writeFileSync(join(output, 'index.html'), 'x');
		writeFileSync(marker, 'x');
		at(OLD, join(output, 'robots.txt'));
		at(RECENT, join(source, 'app.ts'), join(output, 'index.html'), marker);

		return expect(ask()).resolves.toBeNull();
	});
});

describe('when it is not', () => {
	it('builds when a source file is newer than the last finished build', async () => {
		writeFileSync(join(output, 'index.html'), 'x');
		writeFileSync(marker, 'x');
		at(OLD, join(output, 'index.html'), marker);
		writeFileSync(join(source, 'app.ts'), 'x');
		at(RECENT, join(source, 'app.ts'));

		await expect(ask()).resolves.toContain('newer than the build');
	});

	it('**builds when the last build did not finish**', async () => {
		/* An interrupted build cannot have written the marker. */
		writeFileSync(join(output, 'index.html'), 'x');
		writeFileSync(join(source, 'app.ts'), 'x');
		at(RECENT, join(output, 'index.html'), join(source, 'app.ts'));

		await expect(ask()).resolves.toBe('no build has finished here');
	});

	it('builds when something has written into the output since the build ended', async () => {
		writeFileSync(join(source, 'app.ts'), 'x');
		writeFileSync(marker, 'x');
		at(OLD, join(source, 'app.ts'), marker);
		writeFileSync(join(output, 'index.html'), 'x');
		at(RECENT, join(output, 'index.html'));

		await expect(ask()).resolves.toBe('the built client has changed since the last finished build');
	});

	it('builds when there is no client at all', async () => {
		writeFileSync(join(source, 'app.ts'), 'x');
		writeFileSync(marker, 'x');

		await expect(ask()).resolves.toBe('there is no built client');
	});

	it('builds when it cannot read anything to build from', async () => {
		writeFileSync(join(output, 'index.html'), 'x');
		writeFileSync(marker, 'x');
		rmSync(source, { recursive: true });

		await expect(ask()).resolves.toBe('nothing readable to build from');
	});
});
