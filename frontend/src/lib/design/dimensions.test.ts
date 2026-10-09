/* The same app at every screen size. */

import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');

/** The shell's own full height. Something has to be the window, and these are the three that are. */
const IS_THE_WINDOW: readonly RegExp[] = [
	/^routes\/\+layout\.svelte$/,
	/* The card on an empty page, which the three screens outside the application share: sign in,
	   the locked screen, and the desktop client's connect screen. */
	/^lib\/components\/common\/DoorCard\.svelte$/
];

/** Whether a declaration's viewport unit can actually run away with the screen. */
const CEILING = /max-(block-size|inline-size|width|height)\s*:/;
const PINNED = /(min|clamp)\([^;]*\d+(\.\d+)?(px|rem|ch)/;

/* THE ONE MEASUREMENT THAT REALLY IS ABOUT THE WINDOW: the operating system's own caption
 * buttons. */
const WINDOW_FURNITURE = /env\(\s*titlebar-area-/;
const BOUNDED = new RegExp(`${CEILING.source}|${PINNED.source}`);

/* IMPORTANT: A CEILING IS NOT ENOUGH WHEN THE UNIT IS A WIDTH. */

/** What may be `position: fixed`. A MODAL covers the whole application on purpose, including the
 * rail, because that is what "everything else is suspended" looks like. */
const FIXED_ALLOWED: readonly RegExp[] = [
	// The phone's menu sheet (every menu at a phone's width, TOUCH): it stands on the window's foot
	// above the tab bar, which is window geometry by definition, the same case as the drawer below.
	/lib\/components\/common\/ContextMenu\.svelte$/,
	// Modals and full-screen veils: they cover the app deliberately.
	/^lib\/components\/AssetModal\.svelte$/,
	// The stage FILLING the window, where the browser lets no element fill the screen (a phone): it
	// is the full-screen case drawn by hand, so the window is exactly what it measures against.
	/^lib\/components\/player\/MediaStage\.svelte$/,
	// The drawer: a sheet on the menu layer that slides in from the app's edge, its top pinned under
	// the window chrome and the top bar (`--drawer-top`), so it anchors to the app, not the window.
	/^lib\/components\/common\/Drawer\.svelte$/,
	/^lib\/components\/SettingsModal\.svelte$/,
	// The offer to take a drop, window-wide and on an entity's page alike: it is drawn only while
	// something is being HELD over the window, and it says "wherever you let go, it goes here".
	/^lib\/components\/common\/DropOffer\.svelte$/,
	// Its VEIL is fixed and covers everything, which is the modal case.
	/^lib\/components\/shell\/SearchOverlay\.svelte$/,
	// Portalled and script-placed against a trigger.
	/^lib\/components\/common\/Tooltip\.svelte$/,
	// The clear sheet under every portalled menu, popover and select: it covers the whole window on
	// purpose, so a press beside the open list reaches nothing under it, and the list it stands
	// under is itself portalled against the window.
	/^lib\/components\/common\/PageShield\.svelte$/,
	// Reports on the APPLICATION rather than on a screen, and has to work on the sign-in screen
	// where there is no content area at all.
	/^lib\/components\/common\/Toaster\.svelte$/,
	// Dragged by the person using it, to coordinates they chose, and it outlives the screen it was
	// started from.
	/^lib\/components\/player\/MiniPlayer\.svelte$/,
	// The one thing in the app that IS about the window rather than about the app: the desktop
	// window's own title bar, holding the caption area the operating system draws over the page and
	// sized from `env(titlebar-area-*)`.
	/^lib\/components\/shell\/WindowBar\.svelte$/,
	// The app-wide dialog and veil rules, which are the modal case written once.
	/^app\.css$/
];

function sources(): string[] {
	const found: string[] = [];
	const walk = (at: string) => {
		for (const entry of readdirSync(at, { withFileTypes: true })) {
			const path = join(at, entry.name);
			if (entry.isDirectory()) walk(path);
			else if (entry.name.endsWith('.svelte') || entry.name === 'app.css') found.push(path);
		}
	};
	walk(SOURCE);
	return found;
}

interface Offence {
	file: string;
	line: number;
	text: string;
}

/** Every declaration line in a file, with its number, ignoring comments. */
function declarations(body: string): { line: number; text: string }[] {
	// Block comments carry the explanations, and several of them quote the very values this
	// refuses, including the ones above.
	const withoutComments = body.replace(/\/\*[\s\S]*?\*\//g, (block) =>
		block.replace(/[^\n]/g, ' ')
	);
	return withoutComments
		.split('\n')
		.map((text, index) => ({ line: index + 1, text }))
		.filter((row) => row.text.includes(':'));
}

function allowed(file: string, list: readonly RegExp[]): boolean {
	return list.some((pattern) => pattern.test(file));
}

describe('chrome is measured in pixels, not in screens', () => {
	it('no component sizes or positions itself as a share of the window', () => {
		const offences: Offence[] = [];

		for (const path of sources()) {
			const file = relative(SOURCE, path).replaceAll('\\', '/');
			if (allowed(file, IS_THE_WINDOW)) continue;

			for (const { line, text } of declarations(readFileSync(path, 'utf8'))) {
				// A number followed by a viewport unit. The word boundary matters: `overflow` and
				// `revert` both hold the letters and neither is a length.
				if (!/\b\d+(\.\d+)?(vh|vw|dvh|dvw|svh|lvh)\b/.test(text)) continue;
				if (BOUNDED.test(text)) continue;
				if (WINDOW_FURNITURE.test(text)) continue; // see its declaration

				offences.push({ file, line, text: text.trim() });
			}
		}

		expect(
			offences,
			'A dimension written as a share of the window is a different dimension on every screen.\n' +
				'Use a token, or let it fill what is left as a flex or grid child.\n' +
				offences.map((one) => `  ${one.file}:${one.line}  ${one.text}`).join('\n')
		).toEqual([]);
	});
});

describe('the exemption list is a list of real files', () => {
	/* A PATTERN THAT MATCHES NOTHING IS AN EXEMPTION NOBODY IS USING, AND IT LOOKS EXACTLY LIKE
	 * ONE SOMEBODY IS. */
	it('every exemption names a file that exists', () => {
		const files = sources().map((path) => relative(SOURCE, path).replaceAll('\\', '/'));

		const dead = FIXED_ALLOWED.filter((pattern) => !files.some((file) => pattern.test(file)));

		expect(
			dead.map(String),
			'These exemptions match no file. Delete them, or repoint them at where the file went.'
		).toEqual([]);
	});
});

describe('floating chrome anchors to the app, not to the window', () => {
	it('nothing outside the named list is position: fixed', () => {
		const offences: Offence[] = [];

		for (const path of sources()) {
			const file = relative(SOURCE, path).replaceAll('\\', '/');
			if (allowed(file, FIXED_ALLOWED)) continue;

			for (const { line, text } of declarations(readFileSync(path, 'utf8'))) {
				if (!/position\s*:\s*fixed/.test(text)) continue;
				offences.push({ file, line, text: text.trim() });
			}
		}

		expect(
			offences,
			'`position: fixed` is measured against the WINDOW, and the window is not the app: the\n' +
				'rail takes 208px off the left of it. Use `position: absolute` inside the screen, which\n' +
				'is already a positioned container and follows the rail collapsing on its own.\n' +
				'A modal is the exception, and it is in the list.\n' +
				offences.map((one) => `  ${one.file}:${one.line}  ${one.text}`).join('\n')
		).toEqual([]);
	});

	it('nothing measures a width against the whole window either', () => {
		/* The half of the fault that survives fixing the position. */
		const offences: Offence[] = [];

		for (const path of sources()) {
			const file = relative(SOURCE, path).replaceAll('\\', '/');
			if (allowed(file, IS_THE_WINDOW) || allowed(file, FIXED_ALLOWED)) continue;

			for (const { line, text } of declarations(readFileSync(path, 'utf8'))) {
				if (!/100vw/.test(text)) continue;
				// Pinned by an absolute length, so it is a dialog's "this wide, unless the window
				// is smaller" rather than a thing sized to the screen.
				if (PINNED.test(text)) continue;
				if (WINDOW_FURNITURE.test(text)) continue; // see its declaration
				offences.push({ file, line, text: text.trim() });
			}
		}

		expect(
			offences,
			'A width measured against the window is measured against the rail as well.\n' +
				offences.map((one) => `  ${one.file}:${one.line}  ${one.text}`).join('\n')
		).toEqual([]);
	});
});

describe('a screen that changes shape names the width once', () => {
	/* THE FLOORS, and why they are declarations in script rather than media queries in a
	 * stylesheet. */
	const FLOORS = [
		{ where: 'lib/components/common/phone-width.svelte.ts', name: 'PHONE_WIDTH' },
		{ where: 'lib/components/shell/nav.ts', name: 'THEATER_MIN_WIDTH' },
		{ where: 'routes/downloads/narrow.svelte.ts', name: 'NARROW_DOWNLOADS' },
		{ where: 'routes/downloads/narrow.svelte.ts', name: 'FACTS_UNDER_THE_NAME' }
	];

	/** The screens that follow a floor, and must not carry a width of their own. */
	const FOLLOWERS = ['routes/downloads/+page.svelte', 'routes/downloads/PasteBox.svelte'];

	it('every floor is declared once, as an absolute length', () => {
		for (const { where, name } of FLOORS) {
			const body = readFileSync(join(SOURCE, where), 'utf8');
			const match = body.match(new RegExp(`${name}\\s*(?::[^=]*)?=\\s*'([^']+)'`));
			expect(match, `${name} is not declared in ${where}`).not.toBeNull();
			expect(
				match![1],
				`${name} is ${match![1]}; a floor written in anything but pixels is a different floor on every screen`
			).toMatch(/^\((max|min)-width: \d+px\)$/);
		}
	});

	it('a screen that follows a floor holds no width of its own', () => {
		const offences: Offence[] = [];
		for (const where of FOLLOWERS) {
			for (const { line, text } of declarations(readFileSync(join(SOURCE, where), 'utf8'))) {
				if (!/@media[^;]*\b(max|min)-width\s*:/.test(text)) continue;
				offences.push({ file: where, line, text: text.trim() });
			}
		}

		expect(
			offences,
			'The downloads screen reshapes at one width, and that width is NARROW_DOWNLOADS in\n' +
				'routes/downloads/narrow.svelte.ts, because the change is to the markup as well as to the\n' +
				'layout. A media query here is a second copy of the number that only CSS can see.\n' +
				offences.map((one) => `  ${one.file}:${one.line}  ${one.text}`).join('\n')
		).toEqual([]);
	});
});

describe('the furniture is the same size on every screen', () => {
	it('every measurement of the frame is an absolute length', () => {
		/* The tokens themselves, checked for what they ARE rather than for existing. */
		const css = readFileSync(join(SOURCE, 'app.css'), 'utf8');
		const FURNITURE = [
			'--rail-width',
			'--rail-width-collapsed',
			'--topbar-height',
			'--page-footer-height',
			'--page-measure',
			'--control-height',
			'--control-height-sm',
			'--chip-height',
			'--grid-gutter'
		];

		for (const name of FURNITURE) {
			const match = css.match(new RegExp(`^\\s*${name}:\\s*([^;]+);`, 'm'));
			expect(match, `${name} is not declared in app.css`).not.toBeNull();
			const value = match![1].trim();
			expect(value, `${name} is ${value}; the furniture is the same size on every screen`).toMatch(
				/^\d+(\.\d+)?(px|rem)$/
			);
		}
	});
});
