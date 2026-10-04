/*
 * What Sift looks like: a background, an accent and a pair of typefaces, held once for the whole app.
 *
 * FOUR STRINGS AND AN ATTRIBUTE EACH. Almost everything a theme does is in `src/app.css`: the
 * stylesheet holds four bases, six named accents, the faces headings may be set in and the faces
 * everything else may be read in, and stamping `data-base`, `data-accent`, `data-face-display` and `data-face-body` on the document
 * element is the whole of applying one. Nothing here knows a font file, and for the six named
 * accents nothing here knows a colour either.
 *
 * THE SEVENTH ACCENT IS THE ONE EXCEPTION, and it is deliberate rather than a leak. A colour
 * somebody chose cannot be a block in a stylesheet written months earlier, so its five values are
 * worked out by `theme/accent.ts` and set on the root as custom properties. The stylesheet
 * declares the six, and this declares the seventh, in the same five names and the same five roles.
 * That is a theme writing the palette layer, which is what a theme is; it is not a screen reaching
 * past the semantic names, which is the thing the two-layer split exists to forbid.
 *
 * TWO PLACES REMEMBER THE CHOICE, and they are not equal. The SERVER is the source of truth: it is
 * per-account, so it follows somebody to another device and two people sharing an install do not
 * share a look. THIS BROWSER'S STORAGE is a mirror, and exists for one reason: the server's answer
 * arrives one request after the page, so without it every load paints the default and then flips.
 * `static/theme-boot.js` reads the mirror before the first paint; this keeps it fed.
 *
 * The mirror is never trusted over the server. It is applied first because it is instant, and
 * overwritten the moment the real answer lands.
 */

import { fetchSettingValues, onSettingsSaved, saveSettings } from '$lib/settings-ui/settings';
import { clearStored, readStored, writeStored } from '$lib/shell/remembered.svelte';
import { accentFamily, isHex, type AccentFamily } from '$lib/theme/accent';
import CARRIED from '$lib/theme/carried-faces.json';

export const BASE_KEY = 'appearance.theme_base';
export const ACCENT_KEY = 'appearance.theme_accent';

/** The colour a custom accent is derived from, `#rrggbb`.
 *
 * ITS OWN KEY, AND THAT IS ONE KEY PER FACT rather than two keys for one. Which accent is in force
 * and what the custom colour is are two separate answers: a single key holding either a name or a
 * colour would forget the colour the moment somebody tried one of the six and went back, and it
 * could not be declared with a fixed set of choices either, so the server would lose both the
 * menu it draws from and the check that refuses a name no stylesheet matches. */
export const ACCENT_HEX_KEY = 'appearance.theme_accent_hex';

/** The colours somebody kept beside the six, in their order: `#rrggbb` each, at most
 *  `MAX_SWATCHES`, each once. A kept colour is never painted from here: wearing one copies it into
 *  `ACCENT_HEX_KEY`, so it takes the one path a custom colour takes and is derived the same way. */
export const ACCENT_SWATCHES_KEY = 'appearance.theme_accent_swatches';

/** How many colours one person may keep. The server refuses an eleventh; this is the same number,
 *  so the picker can say so before asking. */
export const MAX_SWATCHES = 10;

/** The face the headings and the numbers are set in, and the face everything else is read in.
 *
 * Two keys because they are two choices, and each holds a FACE OF ITS OWN ROLE, by the face's own
 * name: `space-grotesk` on the first, `inter` on the second. They once held a FAMILY's name, which
 * meant that family's display face on one key and its text face on the other; that could not offer
 * one display face over two different text faces without the display face appearing twice in its
 * menu under two family names. The server's settings schema carries a stored family name to the
 * face it meant in each role, and `read` below does the same for this browser's mirror. */
export const FACE_DISPLAY_KEY = 'appearance.theme_face_display';
export const FACE_BODY_KEY = 'appearance.theme_face_body';

/** Where the browser's copy lives. One key holding all three, so the boot script does one read. */
export const MIRROR_KEY = 'sift.theme';

/** The four backgrounds, darkest first. All dark: there is no light theme and this is not the way
 *  one arrives. Obsidian is true black; graphite and chrome are the same grey at two lightnesses;
 *  midnight is the blue one. */
export const BASES = ['obsidian', 'midnight', 'graphite', 'chrome'] as const;

/** The six accents the stylesheet draws, in the order the picker draws them. One family: the same
 *  lightness, hues spaced evenly around the circle from the blue the brand mark is drawn in. They
 *  are named for what they read as, which is not quite a rainbow: even spacing gives a magenta
 *  and a cyan, and no orange. */
export const NAMED_ACCENTS = ['blue', 'magenta', 'red', 'gold', 'green', 'cyan'] as const;

/** The seventh: whatever colour somebody chose, worked out into the same five roles. */
export const CUSTOM_ACCENT = 'custom';

export const ACCENTS = [...NAMED_ACCENTS, CUSTOM_ACCENT] as const;

/** The faces headings and numbers may be set in, each by its own name, in the order the menu
 *  offers them. Every one is checked for tabular figures before it may appear here (see
 *  `lib/design/figures.test.ts`), and each has its own rule in `app.css`. */
export const DISPLAY_FACES = [
	'archivo',
	'space-grotesk',
	'geist-mono',
	'manrope',
	'jetbrains-mono'
] as const;

/** The faces everything else may be read in, the same way. Machine facts are set in these, which
 *  is why the tabular-figure rule binds hardest here: a face with no figures that hold still
 *  (DM Sans) is offered only because its numerals are drawn from the Main face in force. */
export const BODY_FACES = [
	'instrument-sans',
	'inter',
	'geist',
	'public-sans',
	'sora',
	'dm-sans'
] as const;

export type Base = (typeof BASES)[number];
export type Accent = (typeof ACCENTS)[number];
export type DisplayFace = (typeof DISPLAY_FACES)[number];
export type BodyFace = (typeof BODY_FACES)[number];

/** A pairing: one face of each role, designed or chosen to sit together, offered first under one
 *  name. It is a PRESET and nothing is stored about it: pressing one sets both faces, and the
 *  pairing showing as chosen is whichever one has both its faces in force, or none. */
export interface Pairing {
	display: DisplayFace;
	body: BodyFace;
}

/** The pairings, in the order the pane draws them, the default first. */
export const PAIRINGS: readonly Pairing[] = [
	{ display: 'archivo', body: 'instrument-sans' },
	{ display: 'space-grotesk', body: 'inter' },
	{ display: 'geist-mono', body: 'geist' },
	{ display: 'manrope', body: 'public-sans' },
	{ display: 'space-grotesk', body: 'dm-sans' },
	{ display: 'jetbrains-mono', body: 'sora' }
];

/** A pairing as one word, for a group of cards that wants a single value per card. */
export const pairingKey = (pairing: Pairing): string => `${pairing.display}+${pairing.body}`;

/** The pairing whose two faces are both in force, or null when the two menus were set apart. */
export function pairingOf(display: DisplayFace, body: BodyFace): Pairing | null {
	return PAIRINGS.find((one) => one.display === display && one.body === body) ?? null;
}

export interface Choice {
	base: Base;
	accent: Accent;
	/** `#rrggbb`. Only read when `accent` is `custom`; empty until the server has answered. */
	accentHex: string;
	faceDisplay: DisplayFace;
	faceBody: BodyFace;
}

/** What a fresh install looks like, and what the stylesheet's `:root` already is.
 *
 * The custom colour has no default here and cannot have one: a colour written in this file would be
 * a second declaration of a colour, which is the one thing `app.css` is for. The server's
 * registration carries the starting colour, and until it answers there is simply no custom accent
 * to paint, which shows the default blue, which is correct. */
export const DEFAULT_CHOICE: Choice = {
	base: 'midnight',
	accent: 'blue',
	accentHex: '',
	faceDisplay: 'archivo',
	faceBody: 'instrument-sans'
};

/** A value from the server or from storage, or the default. Never trusted straight into the DOM:
 *  a name no rule matches would leave the page half-themed with nothing anywhere saying why. */
function pick<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
	return typeof value === 'string' && (allowed as readonly string[]).includes(value)
		? (value as T)
		: fallback;
}

/**
 * What a FAMILY's name meant in each role, for an answer stored before the faces had names of their
 * own. In such an answer a family's name means its display face on one key and its text face on the
 * other, so the word is read as the face it meant there and nothing anybody chose changes.
 *
 * The browser's mirror is the reason this exists: the server carries its own stored values across
 * in its settings schema, but nothing upgrades a copy in this browser's storage, and without this
 * the first load after an update would paint the default and then flip. A name a face already has
 * (`archivo` and `manrope` as a display face, `geist` as a text face) needs no entry.
 *
 * ONE TABLE, in `theme/carried-faces.json`, because the boot script needs it too, on the frame
 * before this module exists. `scripts/write_theme_boot.js` writes it into that script and
 * `theme-boot.test.ts` holds the two to the same answer. Every value still goes through `pick`,
 * so a face the table names that no list offers is the default rather than a name nothing paints.
 */
const FAMILY_DISPLAY: Readonly<Record<string, string>> = CARRIED.display;
const FAMILY_BODY: Readonly<Record<string, string>> = CARRIED.body;

/** A display face from anything: its own name, a family's older name, or the default. */
function displayFace(
	value: unknown,
	fallback: DisplayFace = DEFAULT_CHOICE.faceDisplay
): DisplayFace {
	const carried =
		typeof value === 'string' && Object.hasOwn(FAMILY_DISPLAY, value)
			? FAMILY_DISPLAY[value]
			: value;
	return pick(carried, DISPLAY_FACES, fallback);
}

/** A text face from anything, the same way. */
function bodyFace(value: unknown, fallback: BodyFace = DEFAULT_CHOICE.faceBody): BodyFace {
	const carried =
		typeof value === 'string' && Object.hasOwn(FAMILY_BODY, value) ? FAMILY_BODY[value] : value;
	return pick(carried, BODY_FACES, fallback);
}

/** The same guard for the one value that is not a name from a list. SHAPE only, which is all there
 *  is to check: every six-digit colour is a colour, and the derivation makes any of them legible. */
function hex(value: unknown, fallback: string): string {
	return typeof value === 'string' && isHex(value) ? value.trim().toLowerCase() : fallback;
}

/** Kept colours from anything: each one of the shape, in one spelling, once, and no more than ten.
 *  The server holds the same rule; this is what a mirror or a change from another window goes
 *  through, so a malformed list draws as the colours in it that are colours. */
function kept(value: unknown): string[] {
	if (!Array.isArray(value)) return [];
	const colours: string[] = [];
	for (const one of value) {
		const colour = hex(one, '');
		if (colour && !colours.includes(colour)) colours.push(colour);
	}
	return colours.slice(0, MAX_SWATCHES);
}

/** The five custom properties an accent block in the stylesheet sets, and the role each one
 *  plays. Named here
 *  so the derived accent fills exactly the same five and cannot invent a sixth. */
const CUSTOM_PROPERTIES: Record<keyof AccentFamily, string> = {
	accent: '--p-accent',
	hover: '--p-accent-hover',
	text: '--p-accent-text',
	bg: '--p-accent-bg',
	ring: '--p-accent-ring-line'
};

/** Take the derived accent off the root, so the named accent's own block is back in force. */
function unpaint(root: HTMLElement): void {
	for (const property of Object.values(CUSTOM_PROPERTIES)) root.style.removeProperty(property);
}

/**
 * Put a derived accent on the root, or take one off. Answers with what it painted, for the mirror.
 *
 * THE GROUNDS ARE READ OFF THE PAGE rather than listed here, and that is the same rule as
 * everywhere else: a base's canvas and its surfaces are colours, `app.css` is the one file allowed
 * to know a colour, and a table of the three bases kept in this file would be a second declaration
 * free to drift the first time a base is retuned. The attributes are stamped before this runs, so
 * what the page answers is the base now in force, which is also why changing the background
 * re-derives the accent rather than leaving one solved against the old canvas.
 *
 * `setProperty` rather than a `style=` attribute written into markup: it is the CSSOM, the same
 * call Svelte's own `style:` directive compiles to, so the policy that forbids inline styles is not
 * involved.
 */
function paint(root: HTMLElement, chosen: string): AccentFamily | null {
	const family = isHex(chosen) ? derivedOn(root, chosen) : null;
	// Nothing honest can be derived from a page that will not say what it is painted on. The named
	// accent's block stays in force, which is a working screen in the default blue rather than a
	// colour nobody can read.
	if (family === null) {
		unpaint(root);
		return null;
	}
	for (const [role, property] of Object.entries(CUSTOM_PROPERTIES)) {
		root.style.setProperty(property, family[role as keyof AccentFamily]);
	}
	return family;
}

/** The family a colour comes to on the base this page is wearing, or null where the page will not
 *  say what it is painted on. The ONE reading of the grounds, for the colour in force and for a
 *  kept colour's dot alike, so a dot cannot be solved against a different canvas from the page. */
function derivedOn(root: HTMLElement, chosen: string): AccentFamily | null {
	const inForce = getComputedStyle(root);
	const ground = {
		canvas: inForce.getPropertyValue('--p-bg').trim(),
		surface3: inForce.getPropertyValue('--p-surface-3').trim(),
		surface4: inForce.getPropertyValue('--p-surface-4').trim()
	};
	if (!isHex(ground.canvas) || !isHex(ground.surface3) || !isHex(ground.surface4)) return null;
	return accentFamily(chosen, ground);
}

/**
 * A kept colour as it will be WORN on the base in force: the family the derivation makes of it,
 * without painting anything. Null for a value that is not a colour, or on a page that cannot say
 * what it is painted on, and then a dot shows the colour as it was kept.
 *
 * The base is the caller's to read (`theme.base`), so a screen drawing these redraws when the
 * background changes; the attribute is stamped before the state moves on, so the page answers
 * for the new base by then.
 */
export function wornAs(colour: string): AccentFamily | null {
	if (typeof document === 'undefined' || !isHex(colour)) return null;
	return derivedOn(document.documentElement, colour.trim().toLowerCase());
}

/** What keeping the colour in force came to: kept, already one of them, or no room for it. */
export type Keeping = 'kept' | 'already' | 'full';

class Theme {
	base = $state<Base>(DEFAULT_CHOICE.base);
	accent = $state<Accent>(DEFAULT_CHOICE.accent);
	accentHex = $state<string>(DEFAULT_CHOICE.accentHex);
	faceDisplay = $state<DisplayFace>(DEFAULT_CHOICE.faceDisplay);
	faceBody = $state<BodyFace>(DEFAULT_CHOICE.faceBody);
	/** The colours this account kept, in their order. Not part of `Choice`: nothing about the look
	 *  in force depends on them, so the mirror and the boot script have no use for them. */
	swatches = $state<string[]>([]);
	/** Whether the server has answered yet. The pane waits for it before drawing which one is on. */
	loaded = $state(false);

	#loading: Promise<void> | null = null;
	/** How many times somebody has chosen something. Only ever compared with itself: a load that
	 *  started before a choice was made must not apply the answer it gets after it. */
	#edits = 0;

	get choice(): Choice {
		return {
			base: this.base,
			accent: this.accent,
			accentHex: this.accentHex,
			faceDisplay: this.faceDisplay,
			faceBody: this.faceBody
		};
	}

	/** Whether the accent in force is a colour somebody chose rather than one of the six. */
	get custom(): boolean {
		return this.accent === CUSTOM_ACCENT;
	}

	/** Put the choice on the page. The same four attributes the boot script writes, so the two
	 *  cannot disagree about how a theme is applied, only about which one, and briefly.
	 *
	 *  The derived accent is painted LAST, after the base is on the page, because it is solved
	 *  against the base's own canvas and surfaces. Answers with the family it painted, or null,
	 *  which is what the mirror carries for the boot script. */
	apply(): AccentFamily | null {
		if (typeof document === 'undefined') return null;
		const root = document.documentElement;
		root.setAttribute('data-base', this.base);
		root.setAttribute('data-accent', this.accent);
		root.setAttribute('data-face-display', this.faceDisplay);
		root.setAttribute('data-face-body', this.faceBody);
		return paint(root, this.custom ? this.accentHex : '');
	}

	/** Read the mirror and apply it, before anything has been asked of the server.
	 *
	 * The boot script has normally done this already; doing it again is what covers the case it
	 * cannot: a client-side navigation into a fresh store, or a browser that refused it. */
	adopt(): void {
		const stored = read();
		this.base = stored.base;
		this.accent = stored.accent;
		this.accentHex = stored.accentHex;
		this.faceDisplay = stored.faceDisplay;
		this.faceBody = stored.faceBody;
		this.apply();
	}

	/** Drop what this browser remembers, on the way out.
	 *
	 * Two accounts on one machine is the case. The mirror exists so a page can be painted before the
	 * server answers, which means that on the first frame after somebody else signs in, it would be
	 * painted in the previous person's taste and corrected a moment later. Nothing private is in a
	 * colour, but somebody else's accent flashing up on your sign-in is a thing you notice and cannot
	 * explain. Cleared here, the next person gets the default for that frame and their own the
	 * instant the server answers.
	 */
	forget(): void {
		this.#loading = null;
		this.#edits += 1;
		this.loaded = false;
		this.base = DEFAULT_CHOICE.base;
		this.accent = DEFAULT_CHOICE.accent;
		this.accentHex = DEFAULT_CHOICE.accentHex;
		this.faceDisplay = DEFAULT_CHOICE.faceDisplay;
		this.faceBody = DEFAULT_CHOICE.faceBody;
		this.swatches = [];
		this.apply();
		// Unavailable storage needs no guard here: the next load then reads nothing and starts from
		// the default, which is the outcome this was reaching for anyway.
		clearStored(MIRROR_KEY);
	}

	/**
	 * Read them from the server, once per page load. Repeated calls join the request in flight.
	 *
	 * THE ANSWER IS DROPPED IF SOMEBODY CHOSE SOMETHING WHILE IT WAS COMING. That is not a nicety:
	 * the app asks for these on every page load, and the appearance pane is a page somebody can
	 * reach and press a swatch on in less time than the round trip takes. The reply then describes
	 * the state BEFORE the change, so applying it would put the old theme back a beat after the new
	 * one appeared, and write the old one into the mirror as well, so a reload would confirm the
	 * revert and the choice would be genuinely lost.
	 */
	load(): Promise<void> {
		if (this.#loading) return this.#loading;
		const chosenBefore = this.#edits;
		this.#loading = (async () => {
			try {
				const values = await fetchSettingValues();
				if (this.#edits !== chosenBefore) return;
				this.base = pick(values.get(BASE_KEY), BASES, DEFAULT_CHOICE.base);
				this.accent = pick(values.get(ACCENT_KEY), ACCENTS, DEFAULT_CHOICE.accent);
				this.accentHex = hex(values.get(ACCENT_HEX_KEY), DEFAULT_CHOICE.accentHex);
				this.faceDisplay = displayFace(values.get(FACE_DISPLAY_KEY));
				this.faceBody = bodyFace(values.get(FACE_BODY_KEY));
				this.swatches = kept(values.get(ACCENT_SWATCHES_KEY));
				mirror(this.choice, this.apply());
			} catch {
				// A preference that could not be read is not worth a message. Whatever the mirror put
				// on the page stays there, and the screen is usable either way.
			} finally {
				this.loaded = true;
			}
		})();
		return this.#loading;
	}

	/** Take a look chosen somewhere else: this account on another device, or another window here.
	 *
	 * ONLY THE PARTS THE BATCH MENTIONS. A save carries just what it changed, so an absent key means
	 * "not this time" and never "back to the default". Treating it as the latter would repaint the
	 * whole theme every time somebody moved an unrelated switch.
	 *
	 * The same `pick` the server's own answer goes through, rather than a second copy of the rule
	 * for what counts as a real base or accent. Two copies is how one of them comes to allow a name
	 * no stylesheet rule matches, and a half-themed page says nothing about why.
	 */
	follow(saved: Record<string, unknown>): void {
		let moved = false;
		if (BASE_KEY in saved) {
			this.base = pick(saved[BASE_KEY], BASES, DEFAULT_CHOICE.base);
			moved = true;
		}
		if (ACCENT_KEY in saved) {
			this.accent = pick(saved[ACCENT_KEY], ACCENTS, DEFAULT_CHOICE.accent);
			moved = true;
		}
		if (ACCENT_HEX_KEY in saved) {
			this.accentHex = hex(saved[ACCENT_HEX_KEY], DEFAULT_CHOICE.accentHex);
			moved = true;
		}
		if (FACE_DISPLAY_KEY in saved) {
			this.faceDisplay = displayFace(saved[FACE_DISPLAY_KEY]);
			moved = true;
		}
		if (FACE_BODY_KEY in saved) {
			this.faceBody = bodyFace(saved[FACE_BODY_KEY]);
			moved = true;
		}
		// Kept colours change no part of the look in force, so they are taken without repainting
		// or writing the mirror.
		if (ACCENT_SWATCHES_KEY in saved) this.swatches = kept(saved[ACCENT_SWATCHES_KEY]);
		// Nothing about the look moved. Left alone rather than re-applied, because the mirror is a
		// write to this browser's storage and every settings save in the app comes through here.
		if (!moved) return;
		mirror(this.choice, this.apply());
	}

	/**
	 * Show a colour WITHOUT choosing it: the app under a marker that is still being dragged.
	 *
	 * ## Why this is separate from `set` rather than a flag on it
	 *
	 * Because a colour being tried and a colour being chosen are two different facts, and only one
	 * of them is worth remembering. A drag across the picker's square is a hundred colours; a save
	 * per pixel is a hundred writes to the server for one decision, and a hundred entries in this
	 * browser's mirror, which is read before the first paint on the next load, so an abandoned
	 * drag would come back as the theme.
	 *
	 * So this paints and nothing else. The state is not moved, the mirror is not written and the
	 * server is not told. What the page is wearing is ahead of what is stored, deliberately and for
	 * as long as somebody is holding the pointer down; the picker's `onchange` calls `set` when they
	 * let go, and that is the one that lands.
	 *
	 * NOT MOVING `accentHex` IS LOAD-BEARING and not merely tidy. `set` starts by comparing the new
	 * value with the one in force and does nothing when they match, so a preview that wrote the
	 * state would make the `set` that follows it a no-op, and the drag would paint the screen
	 * perfectly and save nothing at all.
	 *
	 * `#edits` is deliberately NOT bumped either. That counter exists so a load already in flight
	 * cannot land on top of a CHOICE, and a preview is not one: the worst a landing answer can do
	 * here is repaint the stored colour for one frame, which the next movement corrects. Counting a
	 * preview would throw the server's answer away for the whole page load (the base and both
	 * faces with it) to avoid a flicker in a window measured in milliseconds.
	 *
	 * Only while the seventh accent is the one in force. A preview painted over one of the six would
	 * put its five custom properties on the root underneath a named accent's own block, and the page
	 * would be wearing a colour nobody chose with nothing to take it off again.
	 */
	preview(colour: string): void {
		if (typeof document === 'undefined' || !this.custom) return;
		paint(document.documentElement, colour);
	}

	/** Change one, on the screen first and then on the server. Put back if the server refuses.
	 *
	 * On the screen first because a theme is judged by looking at it: waiting for a round trip to
	 * find out what a colour looks like makes choosing one feel broken even when it works. */
	async set<K extends keyof Choice>(part: K, value: Choice[K]): Promise<void> {
		const previous = this.choice[part];
		if (previous === value) return;
		this.#put(part, value);
		try {
			await saveSettings({ [KEY_OF[part]]: value });
		} catch (error) {
			this.#put(part, previous);
			throw error;
		}
	}

	/**
	 * Keep the colour in force, at the end of the row.
	 *
	 * Answers what came of it rather than throwing for the two refusals a person can meet: the
	 * colour is already kept, or ten are and one has to go first. Both are known here before the
	 * server is asked, from the same number it holds. A save the server refuses puts the row back
	 * and throws, like every other save in here.
	 */
	async keep(): Promise<Keeping> {
		const colour = hex(this.accentHex, '');
		if (!colour) return 'already';
		if (this.swatches.includes(colour)) return 'already';
		if (this.swatches.length >= MAX_SWATCHES) return 'full';
		await this.#keepList([...this.swatches, colour]);
		return 'kept';
	}

	/**
	 * Wear a kept colour: it becomes the custom colour in force, and the custom accent the accent.
	 *
	 * Through `set`, the one path a custom colour takes, so it is derived, painted, mirrored and
	 * saved exactly as a colour chosen in the picker is. The colour first, then the accent: the other
	 * order would paint the old custom colour for a frame before the new one landed.
	 */
	async wear(colour: string): Promise<void> {
		const chosen = hex(colour, '');
		if (!chosen) return;
		await this.set('accentHex', chosen);
		await this.set('accent', CUSTOM_ACCENT);
	}

	/** Stop keeping a colour. Answers where it was, so an Undo can put it back in the same place,
	 *  or -1 when it was not kept. The colour in force does not move: taking a dot out of the row
	 *  is not choosing another accent. */
	async unkeep(colour: string): Promise<number> {
		const at = this.swatches.indexOf(colour);
		if (at < 0) return -1;
		await this.#keepList(this.swatches.filter((one) => one !== colour));
		return at;
	}

	/** Put a colour back where it was: the Undo of `unkeep`. Nothing happens to a colour already
	 *  kept again, or to a row that filled up in between. */
	async keepAt(colour: string, at: number): Promise<void> {
		const chosen = hex(colour, '');
		if (!chosen || this.swatches.includes(chosen) || this.swatches.length >= MAX_SWATCHES) return;
		const next = [...this.swatches];
		next.splice(Math.max(0, Math.min(at, next.length)), 0, chosen);
		await this.#keepList(next);
	}

	/** Move a kept colour one place earlier (-1) or later (1) in the row. Nothing past either end. */
	async move(colour: string, by: -1 | 1): Promise<void> {
		const at = this.swatches.indexOf(colour);
		const to = at + by;
		if (at < 0 || to < 0 || to >= this.swatches.length) return;
		const next = [...this.swatches];
		[next[at], next[to]] = [next[to], next[at]];
		await this.#keepList(next);
	}

	/** The row, on the screen first and then on the server, put back if the server refuses. */
	async #keepList(next: string[]): Promise<void> {
		const previous = this.swatches;
		this.#edits += 1;
		this.swatches = next;
		try {
			await saveSettings({ [ACCENT_SWATCHES_KEY]: next });
		} catch (error) {
			this.swatches = previous;
			throw error;
		}
	}

	/** One of the three, onto the state, the page and the mirror together. Named rather than indexed
	 *  because the three fields have three different types and there is no honest way to write a
	 *  single assignment across them. */
	#put<K extends keyof Choice>(part: K, value: Choice[K]): void {
		this.#edits += 1;
		if (part === 'base') this.base = value as Base;
		else if (part === 'accent') this.accent = value as Accent;
		else if (part === 'accentHex') this.accentHex = value as string;
		else if (part === 'faceDisplay') this.faceDisplay = value as DisplayFace;
		else this.faceBody = value as BodyFace;
		mirror(this.choice, this.apply());
	}
}

const KEY_OF: Record<keyof Choice, string> = {
	base: BASE_KEY,
	accent: ACCENT_KEY,
	accentHex: ACCENT_HEX_KEY,
	faceDisplay: FACE_DISPLAY_KEY,
	faceBody: FACE_BODY_KEY
};

/** The mirror, read. Anything unreadable is the default: a corrupt copy is not worth an error, and
 *  the server is about to say what the answer really is. */
export function read(): Choice {
	try {
		const raw = readStored(MIRROR_KEY);
		if (!raw) return DEFAULT_CHOICE;
		const stored = JSON.parse(raw) as Partial<Record<keyof Choice, unknown>> & { face?: unknown };
		/* An older copy named one PAIRING under `face`, and it is read as both halves, which is
		   exactly what it meant: a family's name was its display face on one attribute and its text
		   face on the other. A copy holding a family's name under either half is carried the same
		   way. Without this, the one load after an update would paint the default and correct
		   itself when the server answered, which is the flash the mirror exists to prevent. */
		return {
			base: pick(stored.base, BASES, DEFAULT_CHOICE.base),
			accent: pick(stored.accent, ACCENTS, DEFAULT_CHOICE.accent),
			accentHex: hex(stored.accentHex, DEFAULT_CHOICE.accentHex),
			faceDisplay: displayFace(stored.faceDisplay, displayFace(stored.face)),
			faceBody: bodyFace(stored.faceBody, bodyFace(stored.face))
		};
	} catch {
		return DEFAULT_CHOICE;
	}
}

/** The mirror, written. Failing to write it costs a flash on the next load and nothing else, so it
 *  is never allowed to fail a save that has already succeeded.
 *
 * THE DERIVED FAMILY IS STORED AS WELL AS THE COLOUR, and that is not a duplicate of a fact. The
 * boot script runs before the module graph exists and cannot import the derivation; the only other
 * way to paint a custom accent before the first frame would be a second copy of the arithmetic in a
 * plain script, which is the one thing that must not exist. So the mirror carries the ANSWER, the
 * same way it already carries a resolved choice rather than a question, and a stale answer is
 * corrected within the same second by the store, like every other thing in here. */
export function mirror(choice: Choice, custom: AccentFamily | null = null): void {
	writeStored(MIRROR_KEY, JSON.stringify({ ...choice, custom }));
}

export const theme = new Theme();

/* Follow the look while the app is open, wherever it was changed.
 *
 * Registered at module scope, like the rating scale's watcher and the record registry's. Without
 * it the theme is only ever read once per page load. So a desktop window, whose page never
 * reloads, would keep whatever it opened in while a browser signed in to the same account showed
 * the new one, and nothing anywhere would say why the two disagreed.
 */
onSettingsSaved((saved) => theme.follow(saved));
