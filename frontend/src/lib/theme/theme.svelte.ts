/*
 * What Sift looks like: a background, an accent and two typefaces, applied by stamping four
 * attributes the stylesheet answers. A chosen seventh accent is derived (`theme/accent.ts`) and
 * put on the root as custom properties. The server holds the choice; this browser keeps a mirror
 * so the first paint is right (`static/theme-boot.js`), overwritten when the server answers.
 */

import { fetchSettingValues, onSettingsSaved, saveSettings } from '$lib/settings-ui/settings';
import { clearStored, readStored, writeStored } from '$lib/shell/remembered.svelte';
import { accentFamily, isHex, type AccentFamily } from '$lib/theme/accent';
import CARRIED from '$lib/theme/carried-faces.json';

export const BASE_KEY = 'appearance.theme_base';
export const ACCENT_KEY = 'appearance.theme_accent';

/** The colour a custom accent is derived from: its own key, so trying a named one keeps it. */
export const ACCENT_HEX_KEY = 'appearance.theme_accent_hex';

/** Kept colours, worn by copying one into `ACCENT_HEX_KEY`, so it is derived the same way. */
export const ACCENT_SWATCHES_KEY = 'appearance.theme_accent_swatches';

/** How many colours one person may keep; the server refuses an eleventh. */
export const MAX_SWATCHES = 10;

/** The display face and the body face, each stored by the face's own name. */
export const FACE_DISPLAY_KEY = 'appearance.theme_face_display';
export const FACE_BODY_KEY = 'appearance.theme_face_body';

/** One key holding all three, so the boot script does one read. */
export const MIRROR_KEY = 'sift.theme';

/** The four backgrounds, darkest first; all dark. */
export const BASES = ['obsidian', 'midnight', 'graphite', 'chrome'] as const;

/** The six accents, in picker order: one lightness, hues evenly spaced from the brand blue. */
export const NAMED_ACCENTS = ['blue', 'magenta', 'red', 'gold', 'green', 'cyan'] as const;

export const CUSTOM_ACCENT = 'custom';

export const ACCENTS = [...NAMED_ACCENTS, CUSTOM_ACCENT] as const;

/** Display faces in menu order, each checked for tabular figures (`lib/design/figures.test.ts`). */
export const DISPLAY_FACES = [
	'archivo',
	'space-grotesk',
	'geist-mono',
	'manrope',
	'jetbrains-mono'
] as const;

/** Body faces, the same way; DM Sans takes its numerals from the Main face in force. */
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

/** One face of each role: a preset, nothing stored, shown chosen when both are in force. */
export interface Pairing {
	display: DisplayFace;
	body: BodyFace;
}

export const PAIRINGS: readonly Pairing[] = [
	{ display: 'archivo', body: 'instrument-sans' },
	{ display: 'space-grotesk', body: 'inter' },
	{ display: 'geist-mono', body: 'geist' },
	{ display: 'manrope', body: 'public-sans' },
	{ display: 'space-grotesk', body: 'dm-sans' },
	{ display: 'jetbrains-mono', body: 'sora' }
];

export const pairingKey = (pairing: Pairing): string => `${pairing.display}+${pairing.body}`;

export function pairingOf(display: DisplayFace, body: BodyFace): Pairing | null {
	return PAIRINGS.find((one) => one.display === display && one.body === body) ?? null;
}

export interface Choice {
	base: Base;
	accent: Accent;
	/** `#rrggbb`, read only when `accent` is `custom`. */
	accentHex: string;
	faceDisplay: DisplayFace;
	faceBody: BodyFace;
}

/** A fresh install; the custom colour's default is the server's: only `app.css` names colours. */
export const DEFAULT_CHOICE: Choice = {
	base: 'midnight',
	accent: 'blue',
	accentHex: '',
	faceDisplay: 'archivo',
	faceBody: 'instrument-sans'
};

/** A value from the server or storage, or the default: an unmatched name half-themes the page. */
function pick<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
	return typeof value === 'string' && (allowed as readonly string[]).includes(value)
		? (value as T)
		: fallback;
}

/**
 * What a family's older name meant in each role, for this browser's mirror, which nothing else
 * upgrades. One table (`theme/carried-faces.json`) shared with the boot script.
 */
const FAMILY_DISPLAY: Readonly<Record<string, string>> = CARRIED.display;
const FAMILY_BODY: Readonly<Record<string, string>> = CARRIED.body;

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

function bodyFace(value: unknown, fallback: BodyFace = DEFAULT_CHOICE.faceBody): BodyFace {
	const carried =
		typeof value === 'string' && Object.hasOwn(FAMILY_BODY, value) ? FAMILY_BODY[value] : value;
	return pick(carried, BODY_FACES, fallback);
}

/** A colour's shape only: the derivation makes any six-digit colour legible. */
function hex(value: unknown, fallback: string): string {
	return typeof value === 'string' && isHex(value) ? value.trim().toLowerCase() : fallback;
}

/** Kept colours from anything: each of the shape, once, at most ten, as the server holds them. */
function kept(value: unknown): string[] {
	if (!Array.isArray(value)) return [];
	const colours: string[] = [];
	for (const one of value) {
		const colour = hex(one, '');
		if (colour && !colours.includes(colour)) colours.push(colour);
	}
	return colours.slice(0, MAX_SWATCHES);
}

/** The five custom properties an accent block sets, so a derived accent fills exactly those. */
const CUSTOM_PROPERTIES: Record<keyof AccentFamily, string> = {
	accent: '--p-accent',
	hover: '--p-accent-hover',
	text: '--p-accent-text',
	bg: '--p-accent-bg',
	ring: '--p-accent-ring-line'
};

function unpaint(root: HTMLElement): void {
	for (const property of Object.values(CUSTOM_PROPERTIES)) root.style.removeProperty(property);
}

/**
 * Put a derived accent on the root, or take one off, answering with what it painted. The grounds
 * are read off the page, which already wears the new base, since only `app.css` names colours.
 */
function paint(root: HTMLElement, chosen: string): AccentFamily | null {
	const family = isHex(chosen) ? derivedOn(root, chosen) : null;
	// No honest derivation without the grounds: the named accent's block stays in force.
	if (family === null) {
		unpaint(root);
		return null;
	}
	for (const [role, property] of Object.entries(CUSTOM_PROPERTIES)) {
		root.style.setProperty(property, family[role as keyof AccentFamily]);
	}
	return family;
}

/** The one reading of the grounds, for the colour in force and a kept colour's dot alike. */
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

/** A kept colour as it would be worn on the base in force; the caller reads `theme.base`. */
export function wornAs(colour: string): AccentFamily | null {
	if (typeof document === 'undefined' || !isHex(colour)) return null;
	return derivedOn(document.documentElement, colour.trim().toLowerCase());
}

export type Keeping = 'kept' | 'already' | 'full';

class Theme {
	base = $state<Base>(DEFAULT_CHOICE.base);
	accent = $state<Accent>(DEFAULT_CHOICE.accent);
	accentHex = $state<string>(DEFAULT_CHOICE.accentHex);
	faceDisplay = $state<DisplayFace>(DEFAULT_CHOICE.faceDisplay);
	faceBody = $state<BodyFace>(DEFAULT_CHOICE.faceBody);
	/** Kept colours; not part of `Choice`, as nothing about the look in force depends on them. */
	swatches = $state<string[]>([]);
	loaded = $state(false);

	#loading: Promise<void> | null = null;
	/** Bumped on every choice, so a load started before one does not apply its answer. */
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

	get custom(): boolean {
		return this.accent === CUSTOM_ACCENT;
	}

	/** Put the choice on the page as the boot script does; the derived accent last, on the base. */
	apply(): AccentFamily | null {
		if (typeof document === 'undefined') return null;
		const root = document.documentElement;
		root.setAttribute('data-base', this.base);
		root.setAttribute('data-accent', this.accent);
		root.setAttribute('data-face-display', this.faceDisplay);
		root.setAttribute('data-face-body', this.faceBody);
		return paint(root, this.custom ? this.accentHex : '');
	}

	/** Read the mirror and apply it, for a client-side navigation or a refused boot script. */
	adopt(): void {
		const stored = read();
		this.base = stored.base;
		this.accent = stored.accent;
		this.accentHex = stored.accentHex;
		this.faceDisplay = stored.faceDisplay;
		this.faceBody = stored.faceBody;
		this.apply();
	}

	/** Drop this browser's mirror on the way out, so the next person never paints in this taste. */
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
		clearStored(MIRROR_KEY);
	}

	/** Read them from the server once per page load, dropping the answer if a choice came first. */
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
				// Not worth a message: whatever the mirror painted stays.
			} finally {
				this.loaded = true;
			}
		})();
		return this.#loading;
	}

	/** Take a look chosen elsewhere, only the parts the batch mentions, through the same `pick`. */
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
		// Kept colours change nothing in force, so no repaint or mirror write.
		if (ACCENT_SWATCHES_KEY in saved) this.swatches = kept(saved[ACCENT_SWATCHES_KEY]);
		// Nothing moved: every settings save comes through here, so the mirror is not rewritten.
		if (!moved) return;
		mirror(this.choice, this.apply());
	}

	/**
	 * Show a colour without choosing it, under a marker still being dragged: paints only, never the
	 * state, the mirror or the server. `accentHex` must not move, or the `set` on release is a
	 * no-op; `#edits` is not bumped, so a load in flight still lands. Only for the seventh accent.
	 */
	preview(colour: string): void {
		if (typeof document === 'undefined' || !this.custom) return;
		paint(document.documentElement, colour);
	}

	/** Change one on the screen first, then on the server; put back if the server refuses. */
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

	/** Keep the colour in force at the end of the row, answering a refusal rather than throwing. */
	async keep(): Promise<Keeping> {
		const colour = hex(this.accentHex, '');
		if (!colour) return 'already';
		if (this.swatches.includes(colour)) return 'already';
		if (this.swatches.length >= MAX_SWATCHES) return 'full';
		await this.#keepList([...this.swatches, colour]);
		return 'kept';
	}

	/** Wear a kept colour through `set`; the colour first, so the old one never paints. */
	async wear(colour: string): Promise<void> {
		const chosen = hex(colour, '');
		if (!chosen) return;
		await this.set('accentHex', chosen);
		await this.set('accent', CUSTOM_ACCENT);
	}

	/** Stop keeping a colour, answering where it was for an Undo, or -1; the accent stays. */
	async unkeep(colour: string): Promise<number> {
		const at = this.swatches.indexOf(colour);
		if (at < 0) return -1;
		await this.#keepList(this.swatches.filter((one) => one !== colour));
		return at;
	}

	/** The Undo of `unkeep`; nothing happens if it is kept again or the row filled up. */
	async keepAt(colour: string, at: number): Promise<void> {
		const chosen = hex(colour, '');
		if (!chosen || this.swatches.includes(chosen) || this.swatches.length >= MAX_SWATCHES) return;
		const next = [...this.swatches];
		next.splice(Math.max(0, Math.min(at, next.length)), 0, chosen);
		await this.#keepList(next);
	}

	async move(colour: string, by: -1 | 1): Promise<void> {
		const at = this.swatches.indexOf(colour);
		const to = at + by;
		if (at < 0 || to < 0 || to >= this.swatches.length) return;
		const next = [...this.swatches];
		[next[at], next[to]] = [next[to], next[at]];
		await this.#keepList(next);
	}

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

	/** One of the three onto the state, the page and the mirror: three fields, three types. */
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

/** The mirror, read; anything unreadable is the default until the server answers. */
export function read(): Choice {
	try {
		const raw = readStored(MIRROR_KEY);
		if (!raw) return DEFAULT_CHOICE;
		const stored = JSON.parse(raw) as Partial<Record<keyof Choice, unknown>> & { face?: unknown };
		/* An older copy's pairing or family name under `face` is read as both halves it meant. */
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

/**
 * The mirror, written, never failing a save. It stores the derived family too: the boot script
 * runs before the derivation can be imported.
 */
export function mirror(choice: Choice, custom: AccentFamily | null = null): void {
	writeStored(MIRROR_KEY, JSON.stringify({ ...choice, custom }));
}

export const theme = new Theme();

/* Follow the look while the app is open: a desktop page never reloads. */
onSettingsSaved((saved) => theme.follow(saved));
