// Whether the client on disk was built from the source that is on disk now.
//
// Its own module, away from the script that acts on the answer, because the answer is a decision
// with edge cases and a script with a top-level `await` and a `process.exit` in it cannot be driven
// from a test.
//
// ## Why a marker, not the oldest output file
//
// `vite build` copies `frontend/static` into the output with the ORIGINAL modification times, so
// the oldest file in a freshly built client is as old as the oldest file in `static`. "Newest input
// is newer than the oldest output" is therefore true of every build the moment it finishes, and the
// client would be rebuilt every time.
//
// The marker answers the worry that rule was for (an interrupted build leaves some files new and
// the rest from the run before) without the accident: the build writes it as its LAST step, so an
// interrupted build has no marker from that run, and a marker that exists means a build ran to
// completion at that moment. Nothing copies it, so nothing can carry a stale time into it.
//
// Still pessimistic in every direction that matters: no marker means build, an unreadable anything
// means build, an output that has been written to since the marker means build. A wasted build
// costs a minute; a wrong skip means every gate downstream tests a client nobody built.

import { readdir, stat } from 'node:fs/promises';
import { join } from 'node:path';

/**
 * Where the marker lives, given the `frontend` folder. Both the writer and the reader ask here.
 *
 * Outside the built client on purpose: nothing in `src/sift/web` is ours to add to. It is all
 * served over http and it all ships in the installer. `.svelte-kit/` is the build's own scratch
 * folder, is already ignored by git, and is never packaged.
 *
 * @param {string} frontend
 * @returns {string}
 */
export function markerPath(frontend) {
	return join(frontend, '.svelte-kit', 'build-marker');
}

/**
 * The newest and oldest modification times under a path, and how many files were seen.
 *
 * @param {string} path
 * @returns {Promise<{ newest: number, oldest: number, count: number }>}
 */
export async function times(path) {
	let newest = 0;
	let oldest = Infinity;
	let count = 0;

	/** @param {string} at */
	async function visit(at) {
		let entry;
		try {
			entry = await stat(at);
		} catch {
			return; // not there: nothing to compare
		}
		if (entry.isDirectory()) {
			for (const child of await readdir(at)) await visit(join(at, child));
			return;
		}
		count += 1;
		newest = Math.max(newest, entry.mtimeMs);
		oldest = Math.min(oldest, entry.mtimeMs);
	}

	await visit(path);
	return { newest, oldest, count };
}

/**
 * When the last build here finished, or null if none has.
 *
 * @param {string} marker
 * @returns {Promise<number | null>}
 */
async function markedAt(marker) {
	try {
		return (await stat(marker)).mtimeMs;
	} catch {
		return null;
	}
}

/**
 * Why the client needs building, or null when the one on disk is current.
 *
 * `name` turns an input path into the short form a person reads in the reason; it defaults to the
 * path itself so this module needs to know nothing about where the repository is.
 *
 * @param {object} asked
 * @param {string} asked.output where the built client is
 * @param {string} asked.marker the file the build writes when it finishes
 * @param {readonly string[]} asked.inputs everything the build reads
 * @param {(path: string) => string} [asked.name]
 * @returns {Promise<string | null>}
 */
export async function reasonToBuild({
	output,
	marker,
	inputs,
	name = (/** @type {string} */ path) => path
}) {
	const built = await times(output);
	if (built.count === 0) return 'there is no built client';

	const finished = await markedAt(marker);
	if (finished === null) return 'no build has finished here';

	// Something has written into the output since the last build ended: a partial build, a deleted
	// file put back, a hand edit. Whatever it was, this client is not the one that was signed off.
	if (built.newest > finished) return 'the built client has changed since the last finished build';

	let newestInput = 0;
	let which = '';
	for (const input of inputs) {
		const seen = await times(input);
		if (seen.newest > newestInput) {
			newestInput = seen.newest;
			which = input;
		}
	}
	if (newestInput === 0) return 'nothing readable to build from'; // wrong tree; let the build say so

	if (newestInput > finished) return `${name(which)} is newer than the build`;
	return null;
}
