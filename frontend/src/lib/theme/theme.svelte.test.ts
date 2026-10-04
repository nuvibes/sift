/** The choice survives a reload, does not flash, and belongs to one account.
 *
 * Three behaviours, and each one is invisible until it is wrong:
 *
 *   - THE MIRROR. The choice lives on the server, which answers one request after the page loads.
 *     Without a copy in this browser, every load paints the default and then changes colour in front
 *     of whoever opened it. So every write goes to storage as well, and the boot script reads it
 *     back before the first paint.
 *   - THE SERVER STILL WINS. The mirror is a cache, not a second opinion. Signing in somewhere the
 *     mirror disagrees (another account on the same machine, or a choice changed on a phone)
 *     must land on what the server says, not on what this browser remembered.
 *   - THE ROLLBACK. The screen changes before the save is confirmed, because judging a colour means
 *     looking at it. A refused save has to put the page back rather than leave it showing something
 *     that was not stored.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { beforeEach, describe, expect, it, vi } from 'vitest';

const fetchSettingValues = vi.fn();
const saveSettings = vi.fn();

vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettingValues: (...args: unknown[]) => fetchSettingValues(...args),
	saveSettings: (...args: unknown[]) => saveSettings(...args),
	// A rating is drawn wherever this reaches, and the rating scale registers its watcher at
	// module scope. A partial mock without this fails the suite at import.
	onSettingsSaved: vi.fn()
}));

const {
	ACCENT_HEX_KEY,
	ACCENT_KEY,
	ACCENT_SWATCHES_KEY,
	BASES,
	BASE_KEY,
	BODY_FACES,
	CUSTOM_ACCENT,
	DEFAULT_CHOICE,
	DISPLAY_FACES,
	FACE_BODY_KEY,
	FACE_DISPLAY_KEY,
	MAX_SWATCHES,
	MIRROR_KEY,
	PAIRINGS,
	mirror,
	pairingOf,
	read,
	theme,
	wornAs
} = await import('./theme.svelte');

/** The four attributes, which is the whole of applying a theme. */
const stamped = () => ({
	base: document.documentElement.getAttribute('data-base'),
	accent: document.documentElement.getAttribute('data-accent'),
	faceDisplay: document.documentElement.getAttribute('data-face-display'),
	faceBody: document.documentElement.getAttribute('data-face-body')
});

/** The same four, as a choice names them, so the two can be compared without a second list. */
const stampOf = (choice: {
	base: string;
	accent: string;
	faceDisplay: string;
	faceBody: string;
}) => ({
	base: choice.base,
	accent: choice.accent,
	faceDisplay: choice.faceDisplay,
	faceBody: choice.faceBody
});

/** A whole choice, for the mirror, without writing the four defaults out again each time. */
const choiceOf = (parts: Partial<typeof DEFAULT_CHOICE>) => ({ ...DEFAULT_CHOICE, ...parts });

beforeEach(() => {
	localStorage.clear();
	fetchSettingValues.mockReset();
	saveSettings.mockReset();
	// The store is a singleton, the same as it is in the app. `forget` is what a sign-out calls and
	// is exactly the reset each test wants: the choice, the mirror and the request in flight.
	theme.forget();
	for (const part of ['base', 'accent', 'face-display', 'face-body']) {
		document.documentElement.removeAttribute(`data-${part}`);
	}
	document.documentElement.removeAttribute('style');
});

describe('the theme a person chose', () => {
	it('is written to this browser as well as to the server', async () => {
		saveSettings.mockResolvedValue(undefined);

		await theme.set('accent', 'gold');

		expect(saveSettings).toHaveBeenCalledWith({ [ACCENT_KEY]: 'gold' });
		expect(read().accent).toBe('gold');
		expect(stamped().accent).toBe('gold');
	});

	it('is put back on the page from that copy, without asking the server', () => {
		mirror(
			choiceOf({ base: 'obsidian', accent: 'cyan', faceDisplay: 'geist-mono', faceBody: 'geist' })
		);

		theme.adopt();

		// This is the no-flash path: the attributes are on the document with no request made at all.
		expect(fetchSettingValues).not.toHaveBeenCalled();
		expect(stamped()).toEqual({
			base: 'obsidian',
			accent: 'cyan',
			faceDisplay: 'geist-mono',
			faceBody: 'geist'
		});
	});

	/* The pairing is two values. An older copy of the mirror holds a single family's name, and it
	   has to be read as both halves: that name meant its display face on one attribute and its
	   text face on the other, so the old word arrives meaning exactly what it meant. Read as
	   nothing, the one load after an update would paint the default pairing and correct itself
	   when the server answered, which is the flash the mirror exists to prevent. */
	it('reads an older copy that named one pairing as both halves of it', () => {
		localStorage.setItem(MIRROR_KEY, JSON.stringify({ base: 'chrome', face: 'manrope' }));

		theme.adopt();

		expect(stamped()).toEqual({
			base: 'chrome',
			accent: 'blue',
			faceDisplay: 'manrope',
			faceBody: 'public-sans'
		});
	});

	/* The same carrying for a copy written while each half held a FAMILY's name. Every family in
	   both roles, so a face that lost its entry in the table would show here as the default. */
	it.each([
		['archivo', 'archivo', 'instrument-sans'],
		['grotesk', 'space-grotesk', 'inter'],
		['geist', 'geist-mono', 'geist'],
		['manrope', 'manrope', 'public-sans']
	])(
		'reads a copy holding the %s family as the face it meant in each role',
		(family, display, body) => {
			localStorage.setItem(MIRROR_KEY, JSON.stringify({ faceDisplay: family, faceBody: family }));

			expect(read().faceDisplay).toBe(display);
			expect(read().faceBody).toBe(body);
		}
	);

	/* The point of the split: any display face over any text face. Five over six, thirty
	   combinations, of which the pairings are six. */
	it('stamps the two faces separately, so any pair can be worn', async () => {
		saveSettings.mockResolvedValue(undefined);

		await theme.set('faceDisplay', 'jetbrains-mono');
		await theme.set('faceBody', 'inter');

		expect(saveSettings).toHaveBeenCalledWith({ [FACE_DISPLAY_KEY]: 'jetbrains-mono' });
		expect(saveSettings).toHaveBeenCalledWith({ [FACE_BODY_KEY]: 'inter' });
		expect(stamped().faceDisplay).toBe('jetbrains-mono');
		expect(stamped().faceBody).toBe('inter');
		expect(pairingOf(theme.faceDisplay, theme.faceBody)).toBeNull();
	});

	it('gives way to the server when the two disagree', async () => {
		// The case that matters: a second account signing in on a machine whose browser remembers
		// the first one's taste. Adopting the mirror is right for the first frame and wrong after.
		mirror(
			choiceOf({ base: 'chrome', accent: 'cyan', faceDisplay: 'geist-mono', faceBody: 'geist' })
		);
		theme.adopt();
		fetchSettingValues.mockResolvedValue(
			new Map([
				[BASE_KEY, 'midnight'],
				[ACCENT_KEY, 'red'],
				[FACE_DISPLAY_KEY, 'manrope'],
				[FACE_BODY_KEY, 'sora']
			])
		);

		await theme.load();

		const answered = {
			base: 'midnight',
			accent: 'red',
			faceDisplay: 'manrope',
			faceBody: 'sora'
		};
		expect(stamped()).toEqual(answered);
		expect(stampOf(read())).toEqual(answered);
	});

	it('ignores a name no stylesheet has a rule for', async () => {
		// A stored value from an older version, or one edited by hand. It cannot be allowed onto the
		// page: nothing would match it, so the app would sit on the default with no way to tell why.
		fetchSettingValues.mockResolvedValue(
			new Map([
				[BASE_KEY, 'solarized'],
				[ACCENT_KEY, 'chartreuse'],
				[FACE_DISPLAY_KEY, 'comic'],
				[FACE_BODY_KEY, 'comic'],
				[ACCENT_HEX_KEY, 'burnt umber']
			])
		);

		await theme.load();

		expect(stamped()).toEqual(stampOf(DEFAULT_CHOICE));
		expect(theme.accentHex).toBe(DEFAULT_CHOICE.accentHex);
	});

	it('puts the page back when the server refuses the save', async () => {
		saveSettings.mockRejectedValue(new Error('nope'));

		await expect(theme.set('base', 'chrome')).rejects.toThrow();

		expect(theme.base).toBe('midnight');
		expect(stamped().base).toBe('midnight');
		expect(read().base).toBe('midnight');
	});

	it('survives a browser that will not store anything', () => {
		// Private mode, a full quota, or storage switched off. A theme that cannot be mirrored costs
		// a flash on the next load; it must not cost an exception on this one.
		const refuse = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
			throw new Error('quota');
		});
		try {
			expect(() =>
				mirror(choiceOf({ base: 'chrome', accent: 'red', faceDisplay: 'geist-mono' }))
			).not.toThrow();
		} finally {
			refuse.mockRestore();
		}
	});

	it('reads a corrupted copy as the default rather than throwing', () => {
		localStorage.setItem(MIRROR_KEY, 'not json at all');
		expect(read()).toEqual(DEFAULT_CHOICE);
	});

	it('is dropped when somebody signs out, so the next person does not see it', async () => {
		saveSettings.mockResolvedValue(undefined);
		await theme.set('accent', 'magenta');
		expect(localStorage.getItem(MIRROR_KEY)).toContain('magenta');

		theme.forget();

		// Nothing left for the next account's first frame to be painted in but the default.
		expect(localStorage.getItem(MIRROR_KEY)).toBeNull();
		expect(read()).toEqual(DEFAULT_CHOICE);
		expect(stamped()).toEqual(stampOf(DEFAULT_CHOICE));
	});
});

describe('a look chosen somewhere else', () => {
	/* Settings are per ACCOUNT, so one person's theme follows them between the desktop app, a
	 * browser and a second window, and the server announces the change. A theme read once per page
	 * load and kept would hide that in a browser, because a reload is a keystroke away; the desktop
	 * window's page never reloads at all, so it would stay in the theme it opened in for ever while
	 * a browser on the same account showed the new one. */
	it("is taken onto the page and into this browser's copy", () => {
		theme.follow({ [BASE_KEY]: 'chrome' });

		expect(theme.base).toBe('chrome');
		expect(stamped().base).toBe('chrome');
		expect(read().base).toBe('chrome');
	});

	/* A save carries only what it changed, so an absent key means "not this time". Reading it as
	 * "back to the default" would repaint the whole theme every time somebody moved an unrelated
	 * switch, and every settings save in the application arrives here. */
	it('leaves alone the parts the change did not mention', () => {
		// Away from the defaults FIRST, or the assertion cannot fail: a guard that wrongly reset an
		// absent key would reset it to the default, which is what the store already held.
		theme.follow({ [BASE_KEY]: 'chrome', [FACE_DISPLAY_KEY]: 'geist-mono' });

		theme.follow({ [ACCENT_KEY]: 'gold' });

		expect(theme.accent).toBe('gold');
		expect(theme.base).toBe('chrome');
		expect(theme.faceDisplay).toBe('geist-mono');
	});

	/* The same `pick` the server's own answer goes through. A second copy of the rule for what
	 * counts as a real accent is how one of them comes to allow a name no stylesheet matches, and a
	 * half-themed page says nothing anywhere about why. */
	it('ignores a name no stylesheet has a rule for', () => {
		theme.follow({ [FACE_BODY_KEY]: 'comic sans' });

		expect(theme.faceBody).toBe(DEFAULT_CHOICE.faceBody);
	});

	/* Nothing about the look moved, so nothing is written. The mirror is a write to this browser's
	 * storage and it would otherwise happen on every settings save in the app. */
	it('writes nothing when the change was about something else entirely', () => {
		theme.follow({ 'playback.volume': 40 });

		expect(localStorage.getItem(MIRROR_KEY)).toBeNull();
	});
});

describe('an accent that is a colour somebody chose', () => {
	/*
	 * The seventh accent has no block in the stylesheet to match, because it is not in the
	 * stylesheet: its five values are worked out from one colour and set on the root as the same
	 * five custom properties a `[data-accent]` block would. So what is checked here is that they go
	 * on, that they come off again the moment one of the six is chosen, and that the mirror carries
	 * the finished five, which is what the boot script paints before the module graph exists.
	 *
	 * The derivation solves against the base's own canvas and surfaces and reads them off the page,
	 * so the page has to be wearing them. They are read out of `app.css` rather than written here:
	 * this file has no business naming a colour, and reading the real ones means the test is against
	 * the real palette.
	 */
	const APP_CSS = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'app.css');

	const ROLES = [
		'--p-accent',
		'--p-accent-hover',
		'--p-accent-text',
		'--p-accent-bg',
		'--p-accent-ring-line'
	];

	/** The midnight base's three grounds, put on the root the way the stylesheet would. */
	function wearMidnight(): void {
		const css = readFileSync(APP_CSS, 'utf8').replace(/\/\*[\s\S]*?\*\//g, ' ');
		const block = /:root,\s*\[data-base='midnight'\]\s*\{([^}]*)\}/.exec(css);
		expect(block, 'the midnight base is not in app.css under that selector').not.toBeNull();
		for (const name of ['--p-bg', '--p-surface-3', '--p-surface-4']) {
			const found = new RegExp(`${name}\\s*:\\s*([^;]+);`).exec(block![1]);
			expect(found, `${name} is not declared there`).not.toBeNull();
			document.documentElement.style.setProperty(name, found![1].trim());
		}
	}

	/** A colour, built rather than typed: a hex written in a screen's source is a second
	 *  declaration of a colour, and this file is not the one allowed to make one. */
	const colour = (red: number, green: number, blue: number): string =>
		'#' + [red, green, blue].map((one) => one.toString(16).padStart(2, '0')).join('');

	const painted = () =>
		Object.fromEntries(
			ROLES.map((role) => [role, document.documentElement.style.getPropertyValue(role)])
		);

	it('paints the five roles, and takes them off when one of the six is chosen again', async () => {
		saveSettings.mockResolvedValue(undefined);
		wearMidnight();

		await theme.set('accentHex', colour(122, 76, 214));
		await theme.set('accent', CUSTOM_ACCENT);

		for (const [role, value] of Object.entries(painted())) {
			expect(value, `${role} was not painted`).toMatch(/^#[0-9a-f]{6}$/);
		}
		// The finished five are in the mirror as well, because the boot script cannot derive them.
		expect(JSON.parse(localStorage.getItem(MIRROR_KEY) ?? '{}').custom).not.toBeNull();

		await theme.set('accent', 'gold');

		for (const [role, value] of Object.entries(painted())) {
			expect(value, `${role} outlived the accent it belonged to`).toBe('');
		}
	});

	it('is shown while it is being chosen, without being saved or remembered', async () => {
		/* A drag across the picker is a hundred colours. Every one of them has to reach the page (that
		   is the whole of what "see it in the app changing live" means), and none of them may
		   reach the server or this browser's copy, or an abandoned drag comes back as the theme. */
		saveSettings.mockResolvedValue(undefined);
		wearMidnight();
		await theme.set('accent', CUSTOM_ACCENT);
		saveSettings.mockClear();
		localStorage.removeItem(MIRROR_KEY);

		theme.preview(colour(214, 76, 122));

		for (const [role, value] of Object.entries(painted())) {
			expect(value, `${role} was not painted`).toMatch(/^#[0-9a-f]{6}$/);
		}
		expect(saveSettings, 'a colour being tried was sent to the server').not.toHaveBeenCalled();
		expect(localStorage.getItem(MIRROR_KEY), 'a colour being tried was remembered').toBeNull();
		expect(theme.accentHex, 'a colour being tried became the choice').toBe('');
	});

	it('still saves the colour when the drag it was previewing ends', async () => {
		/* THE QUIET ONE. `set` does nothing when the value it is handed is already the one in
		   force, so a preview that moved the state would make the save that follows it a
		   no-op, and the picker would paint the screen perfectly and store nothing at all. */
		saveSettings.mockResolvedValue(undefined);
		wearMidnight();
		await theme.set('accent', CUSTOM_ACCENT);
		saveSettings.mockClear();

		const chosen = colour(214, 76, 122);
		theme.preview(chosen);
		await theme.set('accentHex', chosen);

		expect(saveSettings).toHaveBeenCalledWith({ 'appearance.theme_accent_hex': chosen });
		expect(theme.accentHex).toBe(chosen);
	});

	it('paints nothing while one of the six is the accent in force', async () => {
		/* The five derived properties go on the ROOT, over whatever a named accent's own block put
		   there, and nothing would take them off again, so the page would be left wearing a colour
		   nobody chose. */
		saveSettings.mockResolvedValue(undefined);
		wearMidnight();
		await theme.set('accent', 'gold');

		theme.preview(colour(214, 76, 122));

		for (const value of Object.values(painted())) expect(value).toBe('');
	});

	it('leaves the named accent in force when the colour is not a colour', async () => {
		// Nothing honest can be derived from a value that is not one, and the six still work.
		saveSettings.mockResolvedValue(undefined);
		wearMidnight();

		await theme.set('accent', CUSTOM_ACCENT);

		expect(theme.accentHex).toBe('');
		for (const value of Object.values(painted())) expect(value).toBe('');
	});
});

describe('the faces and the pairings', () => {
	const APP_CSS = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'app.css');
	const css = () => readFileSync(APP_CSS, 'utf8').replace(/\/\*[\s\S]*?\*\//g, ' ');
	const ruled = (attribute: string) =>
		new Set([...css().matchAll(new RegExp(`\\[${attribute}='([^']+)'\\]`, 'g'))].map((m) => m[1]));

	/* A face the menu offers and no rule matches would save and then leave the page on the default,
	   with nothing anywhere saying why; a rule no menu offers is a face nobody can reach. Read both
	   ways, out of the stylesheet itself. */
	it('names every face in the stylesheet, each on its own attribute, and nothing else', () => {
		expect([...ruled('data-face-display')].sort()).toEqual([...DISPLAY_FACES].sort());
		expect([...ruled('data-face-body')].sort()).toEqual([...BODY_FACES].sort());
		expect([...ruled('data-base')].sort()).toEqual([...BASES].sort());
	});

	/* The reason the faces were given names of their own: one display face over two text faces
	   would otherwise be one face under two family names in its menu. */
	it('offers each face once, and no name in both menus', () => {
		expect(new Set(DISPLAY_FACES).size).toBe(DISPLAY_FACES.length);
		expect(new Set(BODY_FACES).size).toBe(BODY_FACES.length);
		const both = DISPLAY_FACES.filter((face) => (BODY_FACES as readonly string[]).includes(face));
		expect(both).toEqual([]);
	});

	it('builds every pairing from one face of each role, the default first', () => {
		for (const pairing of PAIRINGS) {
			expect(DISPLAY_FACES).toContain(pairing.display);
			expect(BODY_FACES).toContain(pairing.body);
			expect(pairingOf(pairing.display, pairing.body)).toBe(pairing);
		}
		expect(PAIRINGS[0]).toEqual({
			display: DEFAULT_CHOICE.faceDisplay,
			body: DEFAULT_CHOICE.faceBody
		});
		expect(PAIRINGS).toContainEqual({ display: 'jetbrains-mono', body: 'sora' });
		expect(PAIRINGS).toContainEqual({ display: 'space-grotesk', body: 'dm-sans' });
	});

	/* A server that has not yet carried its stored family names across still answers with them,
	   and the page wears what they meant rather than the default. */
	it("reads a family's name from the server as the face it meant", async () => {
		fetchSettingValues.mockResolvedValue(
			new Map([
				[FACE_DISPLAY_KEY, 'grotesk'],
				[FACE_BODY_KEY, 'grotesk']
			])
		);

		await theme.load();

		expect(stamped().faceDisplay).toBe('space-grotesk');
		expect(stamped().faceBody).toBe('inter');
	});
});

describe('the colours somebody kept', () => {
	/*
	 * Up to ten colours beside the six, kept in their order. Wearing one is choosing it as the custom
	 * colour through the one path a custom colour takes, so it is derived and painted exactly as the
	 * picker's would be; keeping, taking out and moving one change the row and nothing about the
	 * look in force.
	 */
	const APP_CSS = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'app.css');

	function wearMidnight(): void {
		const css = readFileSync(APP_CSS, 'utf8').replace(/\/\*[\s\S]*?\*\//g, ' ');
		const block = /:root,\s*\[data-base='midnight'\]\s*\{([^}]*)\}/.exec(css);
		expect(block).not.toBeNull();
		for (const name of ['--p-bg', '--p-surface-3', '--p-surface-4']) {
			const found = new RegExp(`${name}\\s*:\\s*([^;]+);`).exec(block![1]);
			document.documentElement.style.setProperty(name, found![1].trim());
		}
	}

	/** A colour built rather than typed: this file is not one allowed to name a colour. */
	const colour = (red: number, green: number, blue: number): string =>
		'#' + [red, green, blue].map((one) => one.toString(16).padStart(2, '0')).join('');

	/** `count` different colours. */
	const colours = (count: number) =>
		Array.from({ length: count }, (_, index) => colour(20 + index * 20, 90, 200));

	it('are at most ten', () => {
		expect(MAX_SWATCHES).toBe(10);
	});

	it('are read from the server, each a colour, once, in one spelling, ten at most', async () => {
		const first = colour(122, 76, 214);
		fetchSettingValues.mockResolvedValue(
			new Map<string, unknown>([
				[
					ACCENT_SWATCHES_KEY,
					[first.toUpperCase(), 'burnt umber', first, 16, ...colours(MAX_SWATCHES)]
				]
			])
		);

		await theme.load();

		expect(theme.swatches[0]).toBe(first);
		expect(theme.swatches).toHaveLength(MAX_SWATCHES);
		expect(new Set(theme.swatches).size).toBe(MAX_SWATCHES);
	});

	it('keep the custom colour in force at the end of the row, once', async () => {
		saveSettings.mockResolvedValue(undefined);
		const first = colour(122, 76, 214);
		const second = colour(30, 160, 120);
		await theme.set('accentHex', first);
		expect(await theme.keep()).toBe('kept');
		await theme.set('accentHex', second);
		expect(await theme.keep()).toBe('kept');

		expect(theme.swatches).toEqual([first, second]);
		expect(saveSettings).toHaveBeenLastCalledWith({ [ACCENT_SWATCHES_KEY]: [first, second] });

		saveSettings.mockClear();
		expect(await theme.keep()).toBe('already');
		expect(saveSettings).not.toHaveBeenCalled();
	});

	it('refuse an eleventh without asking the server, and keep the ten', async () => {
		saveSettings.mockResolvedValue(undefined);
		theme.follow({ [ACCENT_SWATCHES_KEY]: colours(MAX_SWATCHES) });
		await theme.set('accentHex', colour(250, 240, 150));
		saveSettings.mockClear();

		expect(await theme.keep()).toBe('full');

		expect(saveSettings).not.toHaveBeenCalled();
		expect(theme.swatches).toEqual(colours(MAX_SWATCHES));
	});

	it('are put back when the server refuses the row', async () => {
		saveSettings.mockResolvedValue(undefined);
		await theme.set('accentHex', colour(122, 76, 214));
		saveSettings.mockRejectedValue(new Error('refused'));

		await expect(theme.keep()).rejects.toThrow('refused');

		expect(theme.swatches).toEqual([]);
	});

	it('are worn through the one path a custom colour takes, and painted as it is', async () => {
		saveSettings.mockResolvedValue(undefined);
		wearMidnight();
		const kept = colour(250, 240, 150);
		theme.follow({ [ACCENT_SWATCHES_KEY]: [kept] });

		await theme.wear(kept);

		expect(saveSettings).toHaveBeenNthCalledWith(1, { [ACCENT_HEX_KEY]: kept });
		expect(saveSettings).toHaveBeenNthCalledWith(2, { [ACCENT_KEY]: CUSTOM_ACCENT });
		expect([theme.accent, theme.accentHex]).toEqual([CUSTOM_ACCENT, kept]);
		// What a kept dot is drawn in is exactly what wearing it paints, because both come from one
		// reading of the grounds and one derivation.
		const worn = wornAs(kept);
		expect(worn).not.toBeNull();
		expect(document.documentElement.style.getPropertyValue('--p-accent')).toBe(worn!.accent);
		// A pale yellow cannot carry white words on a dark base, so it is worn deeper than kept.
		expect(worn!.accent).not.toBe(kept);
	});

	it('come out with Undo putting one back where it was, and the accent in force stays', async () => {
		saveSettings.mockResolvedValue(undefined);
		const row = colours(4);
		theme.follow({ [ACCENT_SWATCHES_KEY]: row });
		await theme.wear(row[2]);

		expect(await theme.unkeep(row[2])).toBe(2);
		expect(theme.swatches).toEqual([row[0], row[1], row[3]]);
		expect(theme.accentHex).toBe(row[2]);
		expect(await theme.unkeep(row[2])).toBe(-1);

		await theme.keepAt(row[2], 2);
		expect(theme.swatches).toEqual(row);
		expect(saveSettings).toHaveBeenLastCalledWith({ [ACCENT_SWATCHES_KEY]: row });
	});

	it('move one place along the row, and nowhere past either end', async () => {
		saveSettings.mockResolvedValue(undefined);
		const row = colours(3);
		theme.follow({ [ACCENT_SWATCHES_KEY]: row });

		await theme.move(row[0], 1);
		expect(theme.swatches).toEqual([row[1], row[0], row[2]]);
		await theme.move(row[2], -1);
		expect(theme.swatches).toEqual([row[1], row[2], row[0]]);

		saveSettings.mockClear();
		await theme.move(row[1], -1);
		await theme.move(row[0], 1);
		expect(saveSettings).not.toHaveBeenCalled();
	});

	it('follow a change made in another window without writing the look', () => {
		const row = colours(2);
		theme.follow({ [ACCENT_SWATCHES_KEY]: row });

		expect(theme.swatches).toEqual(row);
		expect(localStorage.getItem(MIRROR_KEY)).toBeNull();
	});
});
