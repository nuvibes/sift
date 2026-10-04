/*
 * The same app at every screen size.
 *
 * Sift is a desktop application on screens from about 1024 to about 3840 pixels wide, and it has to
 * be ONE design across that range rather than a small one that has been inflated. Two habits break
 * that, and neither of them looks wrong in the file it is written in.
 *
 * ## 1. A dimension written as a share of the window
 *
 * `34vh` is 306 pixels on a laptop and 583 on a 32-inch monitor. The same panel, nearly twice the
 * size, and nobody chose it. It reads as correct in the stylesheet, it reads as correct on
 * whichever screen it was written on, and the only way to notice is to open the app on a different
 * one.
 *
 * ## 2. A floating thing centred on the WINDOW
 *
 * `position: fixed` with `left: 50%` centres on the browser window, and the window is not the
 * app. The rail takes 208 pixels off the left, so a bar centred that way sits 104 off-centre, and a
 * ceiling of `calc(100vw - 32px)` is measured against a width the app does not have. On a narrow
 * window it runs UNDERNEATH THE RAIL, as a bulk-selection bar centred that way does.
 *
 * ## Why this reads components rather than the token file
 *
 * The tokens are all correct. The mistake happens one declaration at a time at a call site: the
 * same shape as the destructive red, where a gate on `app.css` is structurally unable to see
 * components spending it as a text colour. A gate on a rule about how something is USED has to read
 * the places it is used.
 */

import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');

/**
 * The shell's own full height. Something has to be the window, and these are the three that are.
 *
 * The root of the application, and the two screens drawn without it: sign-in and the locked
 * screen, which are not inside the app yet and so have no content area to measure against.
 */
const IS_THE_WINDOW: readonly RegExp[] = [
	/^routes\/\+layout\.svelte$/,
	/* The card on an empty page, which the three screens outside the application share: sign in,
	   the locked screen, and the desktop client's connect screen. One component, so one entry on
	   this list rather than one per screen. */
	/^lib\/components\/common\/DoorCard\.svelte$/
];

/**
 * Whether a declaration's viewport unit can actually run away with the screen.
 *
 * Two shapes cannot, and they are the honest uses:
 *
 * - **A ceiling.** `max-block-size: 40vh` on a scrolling list inside a dialog says "no taller than
 *   fits", which is true on every screen. It is a limit, not a size.
 * - **A bound.** `min(460px, calc(100vw - 32px))` and `clamp(16px, 5vh, 56px)` are pinned by an
 *   absolute length in the same declaration, so the value has a hard ceiling in pixels whatever
 *   the screen does. That is a dialog saying "this wide, unless the window is smaller".
 *
 * What is refused is a BARE one (`block-size: 34vh`, `inset-block-start: 18vh`), where the
 * number simply is a share of the screen and grows without limit: a panel appearing at a visibly
 * different size and place depending on the monitor.
 *
 * Reading the shape rather than keeping a list of files is deliberate. A list of exempt files is a
 * list that grows every time somebody has a reason, and a file exempted for its dialog is then
 * exempt for everything else in it too.
 */
const CEILING = /max-(block-size|inline-size|width|height)\s*:/;
const PINNED = /(min|clamp)\([^;]*\d+(\.\d+)?(px|rem|ch)/;

/*
 * THE ONE MEASUREMENT THAT REALLY IS ABOUT THE WINDOW: the operating system's own caption buttons.
 *
 * Everything this file refuses is refused because the window is not the application: the rail
 * takes 208 pixels off the left of it, so a share of the window is a share of something the app
 * does not own. The desktop shell draws its window with no title bar and the minimise, maximise and
 * close overlaid on the page, and the top bar then has to end before they begin. That distance is
 * the window's width less `env(titlebar-area-width)`, and there is no other way to write it: the
 * environment variable gives the part of the strip the PAGE gets, and only the window knows the
 * whole.
 *
 * So a viewport unit standing beside `env(titlebar-area-...)` is exempt, and nothing else is. It is
 * deliberately keyed to the DECLARATION rather than to a file: the top bar is otherwise ordinary
 * application furniture and every other measurement in it is still checked, which a file-level
 * exemption would have given up. A `100vw` written anywhere near this one, without the environment
 * variable, still fails.
 */
const WINDOW_FURNITURE = /env\(\s*titlebar-area-/;
const BOUNDED = new RegExp(`${CEILING.source}|${PINNED.source}`);

/*
 * IMPORTANT: A CEILING IS NOT ENOUGH WHEN THE UNIT IS A WIDTH.
 *
 * `max-inline-size: calc(100vw - 32px)` looks bounded and is not: it bounds the element against the
 * WINDOW, and the window is 208 pixels wider than the app. Fixing a bar's centring alone would
 * leave it allowed to be wider than the space it sits in, which on a narrow window still reaches
 * under the rail.
 *
 * So the width check below honours only `PINNED`. A `min()` against an absolute length really is a
 * hard cap in pixels; a `max-` against the viewport is a cap against the wrong thing.
 */

/**
 * What may be `position: fixed`.
 *
 * A MODAL covers the whole application on purpose, including the rail, because that is what
 * "everything else is suspended" looks like. A PORTALLED popup is positioned against the thing that
 * opened it rather than against a region, and is placed by script. Everything else (a selection
 * bar, a pager, a banner) belongs to the screen and anchors to the screen.
 */
const FIXED_ALLOWED: readonly RegExp[] = [
	// The phone's menu sheet (every menu at a phone's width, TOUCH): it stands on the window's foot
	// above the tab bar, which is window geometry by definition, the same case as the drawer below.
	/lib\/components\/common\/ContextMenu\.svelte$/,
	// Modals and full-screen veils: they cover the app deliberately.
	/^lib\/components\/AssetModal\.svelte$/,
	// The stage FILLING the window, where the browser lets no element fill the screen (a phone):
	// it is the full-screen case drawn by hand, so the window is exactly what it measures against.
	/^lib\/components\/player\/MediaStage\.svelte$/,
	// The drawer: a sheet on the menu layer that slides in from the app's edge, its top pinned under
	// the window chrome and the top bar (`--drawer-top`), so it anchors to the app, not the window.
	/^lib\/components\/common\/Drawer\.svelte$/,
	/^lib\/components\/SettingsModal\.svelte$/,
	// The offer to take a drop, window-wide and on an entity's page alike: it is drawn only while
	// something is being HELD over the window, and it says "wherever you let go, it goes here". An
	// offer inset to the content column would leave the rail as a place a drop meant something else.
	/^lib\/components\/common\/DropOffer\.svelte$/,
	// Its VEIL is fixed and covers everything, which is the modal case. The panel inside it is
	// `position: absolute` against the content column, which is the rule working as intended.
	/^lib\/components\/shell\/SearchOverlay\.svelte$/,
	// Portalled and script-placed against a trigger.
	/^lib\/components\/common\/Tooltip\.svelte$/,
	// The clear sheet under every portalled menu, popover and select: it covers the whole window
	// on purpose, so a press beside the open list reaches nothing under it, and the list it stands
	// under is itself portalled against the window.
	/^lib\/components\/common\/PageShield\.svelte$/,
	// Reports on the APPLICATION rather than on a screen, and has to work on the sign-in screen
	// where there is no content area at all. Bottom-right, which is the corner the rail is not in.
	/^lib\/components\/common\/Toaster\.svelte$/,
	// Dragged by the person using it, to coordinates they chose, and it outlives the screen it was
	// started from. Its offsets are pixels from a drag, never a percentage of the window.
	/^lib\/components\/player\/MiniPlayer\.svelte$/,
	// The one thing in the app that IS about the window rather than about the app: the desktop
	// window's own title bar, holding the caption area the operating system draws over the page and
	// sized from `env(titlebar-area-*)`. Those are window coordinates, so anything but `fixed` would
	// put the strip and its drag region somewhere other than the top of the window.
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
	// refuses, including the ones above. Stripped rather than skipped line by line, because a
	// comment spans lines and a per-line skip would only catch the first of them.
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
	/*
	 * A PATTERN THAT MATCHES NOTHING IS AN EXEMPTION NOBODY IS USING, AND IT LOOKS EXACTLY LIKE ONE
	 * SOMEBODY IS.
	 *
	 * An entry naming a file that has moved stays behind pointing at nothing, still reading as a
	 * considered decision, while the component it was written for fails the gate under its new
	 * name. The gate catches the new file, which is the half that works; nothing else would catch
	 * the dead entry.
	 *
	 * So every pattern has to match something. An exemption for a file that has gone is deleted
	 * with the file, and one for a file that has been renamed fails here rather than surviving as a
	 * sentence about a path that is not there.
	 */
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
		/*
		 * The half of the fault that survives fixing the position.
		 *
		 * A bar centred on the window AND capped at `100vw - 32px`, with only the centring fixed,
		 * is still allowed to be wider than the space it sits in, which on a narrow window still
		 * reaches under the rail, and would look fixed on the screen it was checked on. `100%` of
		 * a positioned container is the right measure; the container knows how wide the app is and
		 * `100vw` never can.
		 */
		const offences: Offence[] = [];

		for (const path of sources()) {
			const file = relative(SOURCE, path).replaceAll('\\', '/');
			if (allowed(file, IS_THE_WINDOW) || allowed(file, FIXED_ALLOWED)) continue;

			for (const { line, text } of declarations(readFileSync(path, 'utf8'))) {
				if (!/100vw/.test(text)) continue;
				// Pinned by an absolute length, so it is a dialog's "this wide, unless the window is
				// smaller" rather than a thing sized to the screen. NOT `BOUNDED`. See the note by
				// its declaration: a `max-` against the viewport is a cap against the wrong thing.
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
	/*
	 * THE FLOORS, and why they are declarations in script rather than media queries in a stylesheet.
	 *
	 * Two screens reshape below a width rather than merely reflowing: Theater refuses to be drawn at
	 * all under 1024, and Downloads puts its paste button under its box and its search under its
	 * chips under 720. Both of those are decisions about WHAT IS DRAWN, and a media query cannot
	 * reach the markup: a rail shrunk in CSS alone would go on rendering the wide brand lockup and
	 * hang it over the library.
	 *
	 * So each floor is one exported query, and this holds it to two things: it is an absolute length
	 * (a floor written as a share of the screen is a different floor on every screen, which is this
	 * whole file's subject), and the screens that obey it hold no second copy of the number. The
	 * second half is the one that bites: a component that reshapes at 719 in script and at 767 in its
	 * own stylesheet is a band where the layout and the markup disagree, and nobody looks at that
	 * band.
	 */
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
		/*
		 * The tokens themselves, checked for what they ARE rather than for existing.
		 *
		 * `interaction.test.ts` proves these are declared. This proves each is a fixed length: a
		 * rail declared as `12%` would satisfy that test and break the whole rule, and it is exactly
		 * the sort of change that gets made to fix one screen.
		 */
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
