/**
 * Whether the client on disk was built from the source on disk, and the rebuild it saves.
 *
 * A production build is the most expensive step in a local gate run, and it is paid twice: once
 * for the Python suite, which asserts things about the built page, and once for the end-to-end run,
 * which serves it. `scripts/build_if_stale.js` exists to skip the second one when nothing has
 * moved.
 *
 * A rule comparing the newest input against the OLDEST file in the output never skips: `vite build`
 * copies `static/` in with the original modification times, so the oldest file in a client built
 * five seconds ago is as old as the oldest file in `static/`, and the condition is true of every
 * build the moment it finishes.
 *
 * Tested here rather than trusted because the failure is silent in both directions: a check that
 * never skips only costs a minute a run, and a check that skips wrongly means every gate downstream
 * measures a client nobody built. The decision lives in `scripts/lib/build-freshness.js` precisely
 * so it can be driven with a tree made for the purpose.
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

/** Seconds since the epoch, so a test can put files in a known order without waiting. */
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
		/* The case an oldest-file rule can never reach. */
		writeFileSync(join(source, 'app.ts'), 'x');
		writeFileSync(join(output, 'index.html'), 'x');
		writeFileSync(marker, 'x');
		at(RECENT, join(source, 'app.ts'), join(output, 'index.html'), marker);

		return expect(ask()).resolves.toBeNull();
	});

	it('**skips it even when the output holds a file older than the source**', () => {
		/* The whole fault, in one assertion. `robots.txt` is copied out of `static/` with its
		   own modification time, so the oldest file in a fresh build predates every source file
		   and an oldest-file rule reads exactly that as "the client is out of date". */
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
		/* The property the marker is for: a build that was interrupted leaves some of its files
		   new and the rest from the run before. It cannot have written the marker, so there is
		   nothing saying a build ever completed. */
		writeFileSync(join(output, 'index.html'), 'x');
		writeFileSync(join(source, 'app.ts'), 'x');
		at(RECENT, join(output, 'index.html'), join(source, 'app.ts'));

		await expect(ask()).resolves.toBe('no build has finished here');
	});

	it('builds when something has written into the output since the build ended', async () => {
		/* A half-finished second build, a file put back by hand. Whatever it was, what is on disk is
		   not what the marker is vouching for. */
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
		/* The wrong tree, or a checkout half-deleted. The build is the thing that should say so. */
		writeFileSync(join(output, 'index.html'), 'x');
		writeFileSync(marker, 'x');
		rmSync(source, { recursive: true });

		await expect(ask()).resolves.toBe('nothing readable to build from');
	});
});
