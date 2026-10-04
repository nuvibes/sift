/*
 * One component owns the dialog primitives. Nothing else may reach for them.
 *
 * Every other pop-up building block in the app is wrapped once and reused: the confirm box, the
 * dropdown, the switch, the right-click menu, the progress bar. A dialog is the same: components
 * each hand-writing the same ten lines for the portal, the dimmed backdrop, the layering and the
 * close behaviour would make every fix to any of it a fix to be found in every copy. A page left
 * unclickable after a menu closes, or a pop-up invisible in fullscreen, is a fault in exactly
 * this scaffolding.
 *
 * So the rule is the whole of it: the library's dialog parts are imported in one file. A component
 * that wants a sheet uses `Modal`, and one that wants something `Modal` cannot do changes `Modal`.
 *
 * Static, and honest about being static: it reads the source rather than a running page. What it
 * proves is that there is one door, not that the door is drawn correctly.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

/** The whole of `src`, resolved from this file rather than from the working directory: the runner
 *  is started from more than one place and a relative root silently surveys nothing. */
const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');

/** The one file allowed to import them, as a path from `src`. */
const THE_DOOR = 'lib/components/common/Modal.svelte';

/**
 * The two primitives a modal sheet is built from.
 *
 * Both, not just the plain one. `AlertDialog` is the same scaffolding with a different role, and
 * leaving it out would have left the confirm box and the PIN box free to drift apart from every
 * other sheet, which is the fault this check exists to end, wearing a different name.
 */
const PRIMITIVES = ['Dialog', 'AlertDialog'];

function everySourceFile(dir: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(dir)) {
		if (entry === 'node_modules') continue;
		const path = join(dir, entry);
		if (statSync(path).isDirectory()) found.push(...everySourceFile(path));
		else if (entry.endsWith('.svelte') || entry.endsWith('.ts')) found.push(path);
	}
	return found;
}

/** What a file takes out of the component library, by name. */
function importsFromLibrary(source: string): string[] {
	const taken: string[] = [];
	for (const match of source.matchAll(/import\s+\{([^}]*)\}\s+from\s+['"]bits-ui['"]/g)) {
		for (const name of match[1].split(',')) {
			const bare = name
				.replace(/\btype\b/, '')
				.trim()
				.split(/\s+as\s+/)[0];
			if (bare) taken.push(bare);
		}
	}
	return taken;
}

describe('the dialog primitives have one importer', () => {
	const files = everySourceFile(SOURCE).map((path) => ({
		where: relative(SOURCE, path).split('\\').join('/'),
		source: readFileSync(path, 'utf8')
	}));

	it('surveys the interface rather than an empty tree', () => {
		// Without this the whole check passes by finding nothing, which is what a wrong root looks
		// like from the outside.
		expect(files.length).toBeGreaterThan(100);
		expect(files.some((file) => file.where === THE_DOOR)).toBe(true);
	});

	it('is imported by Modal and by nothing else', () => {
		const reaching = files
			.filter((file) => file.where !== THE_DOOR)
			.filter((file) => importsFromLibrary(file.source).some((name) => PRIMITIVES.includes(name)))
			.map((file) => file.where);

		expect(reaching).toEqual([]);
	});

	it('is imported by Modal, so the rule above is not vacuous', () => {
		// The other way round. Without this, deleting the import from `Modal`, or renaming the
		// file, would leave a green check over an app with no dialog in it at all.
		const door = files.find((file) => file.where === THE_DOOR);
		expect(importsFromLibrary(door?.source ?? '')).toEqual(expect.arrayContaining(PRIMITIVES));
	});
});
