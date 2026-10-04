import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick } from 'svelte';

/*
 * The lettering and the backgrounds, as the Appearance pane offers them.
 *
 * A pairing is a PRESET of two faces, one of each role, and pressing one sets both menus under it.
 * Which card shows as chosen is worked out from the two faces in force, so the pair a person took
 * apart with the menus marks no card at all rather than the card it started from.
 */

const fetchSettingValues = vi.fn();
const saveSettings = vi.fn();

vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettingValues: (...args: unknown[]) => fetchSettingValues(...args),
	saveSettings: (...args: unknown[]) => saveSettings(...args),
	onSettingsSaved: vi.fn()
}));

const { default: ThemeChoices, keptLabel } = await import('./ThemeChoices.svelte');
const { theme, ACCENT_SWATCHES_KEY, FACE_BODY_KEY, FACE_DISPLAY_KEY, MAX_SWATCHES, wornAs } =
	await import('$lib/theme/theme.svelte');
const { phoneWidth } = await import('$lib/components/common/phone-width.svelte');
const { readFileSync } = await import('node:fs');
const { dirname, resolve } = await import('node:path');
const { fileURLToPath } = await import('node:url');

let host: HTMLElement;

beforeEach(() => {
	localStorage.clear();
	saveSettings.mockReset();
	saveSettings.mockResolvedValue(undefined);
	theme.forget();
});

afterEach(() => host?.remove());

function render(): void {
	host = document.createElement('div');
	document.body.append(host);
	mount(ThemeChoices, { target: host });
	flushSync();
}

/** The cards of one group, by the group's name, as name and chosen state. */
function cards(group: string): { name: string; chosen: boolean; press: () => void }[] {
	const root = host.querySelector(`[aria-label="${group}"]`);
	expect(root, `no group called ${group}`).not.toBeNull();
	return [...root!.querySelectorAll<HTMLButtonElement>('[role="radio"]')].map((card) => ({
		name: card.textContent?.replace(/\s+/g, ' ').trim() ?? '',
		chosen: card.getAttribute('aria-checked') === 'true',
		press: () => card.click()
	}));
}

describe('the pairings', () => {
	it('are six, each named for its two faces, the default marked', () => {
		render();
		const names = cards('Font pairing').map((card) => card.name);
		expect(names).toHaveLength(6);
		expect(names.some((name) => name.includes('JetBrains Mono and Sora'))).toBe(true);
		expect(names.some((name) => name.includes('Space Grotesk and DM Sans'))).toBe(true);
		expect(names[0]).toContain('Archivo and Instrument Sans');
		expect(cards('Font pairing').filter((card) => card.chosen)).toHaveLength(1);
		expect(cards('Font pairing')[0].chosen).toBe(true);
	});

	it('set both menus when one is pressed, and mark that card', async () => {
		render();
		cards('Font pairing')
			.find((card) => card.name.includes('JetBrains Mono and Sora'))!
			.press();
		await tick();
		await tick();
		flushSync();

		expect(theme.faceDisplay).toBe('jetbrains-mono');
		expect(theme.faceBody).toBe('sora');
		expect(saveSettings).toHaveBeenCalledWith({ [FACE_DISPLAY_KEY]: 'jetbrains-mono' });
		expect(saveSettings).toHaveBeenCalledWith({ [FACE_BODY_KEY]: 'sora' });
		const chosen = cards('Font pairing').filter((card) => card.chosen);
		expect(chosen.map((card) => card.name.includes('JetBrains Mono and Sora'))).toEqual([true]);
	});

	it('tell the two Space Grotesk pairings apart by their secondary font', async () => {
		render();
		cards('Font pairing')
			.find((card) => card.name.includes('Space Grotesk and DM Sans'))!
			.press();
		await tick();
		await tick();
		flushSync();

		expect([theme.faceDisplay, theme.faceBody]).toEqual(['space-grotesk', 'dm-sans']);
		const chosen = cards('Font pairing').filter((card) => card.chosen);
		expect(chosen.map((card) => card.name.includes('Space Grotesk and DM Sans'))).toEqual([true]);
	});

	it('mark no card when the two faces are not one of them', async () => {
		render();
		await theme.set('faceDisplay', 'jetbrains-mono');
		await theme.set('faceBody', 'inter');
		flushSync();

		expect(cards('Font pairing').filter((card) => card.chosen)).toEqual([]);
	});
});

describe('the backgrounds', () => {
	it('are four, true black first', () => {
		render();
		const names = cards('Background').map((card) => card.name);
		expect(names).toHaveLength(4);
		expect(names[0]).toContain('Obsidian');
	});
});

describe('the saved colors', () => {
	/*
	 * A second row under the six: each kept colour a radio named by its hex, painted as it will be
	 * WORN on the base in force, the one in force marked. Save at the foot of the picker keeps the
	 * custom colour in force, and at ten it says how to make room instead.
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

	afterEach(() => document.documentElement.removeAttribute('style'));

	const colour = (red: number, green: number, blue: number): string =>
		'#' + [red, green, blue].map((one) => one.toString(16).padStart(2, '0')).join('');
	const colours = (count: number) =>
		Array.from({ length: count }, (_, index) => colour(20 + index * 20, 90, 200));

	/** A colour as the page reports a painted background, for comparing with a dot. */
	const asRgb = (hex: string) => {
		const at = (index: number) => parseInt(hex.slice(index, index + 2), 16);
		return `rgb(${at(1)}, ${at(3)}, ${at(5)})`;
	};

	it('draw no row while nothing is kept', () => {
		render();
		expect(host.querySelector('[aria-label="Saved colors"]')).toBeNull();
	});

	it('are a row of radios named by their hex, the one in force marked', async () => {
		const row = colours(3);
		theme.follow({ [ACCENT_SWATCHES_KEY]: row });
		await theme.wear(row[1]);
		render();

		const kept = cards('Saved colors');
		expect(kept.map((one) => one.chosen)).toEqual([false, true, false]);
		const names = [...host.querySelectorAll('[aria-label="Saved colors"] [role="radio"]')].map(
			(one) => one.getAttribute('aria-label')
		);
		expect(names).toEqual(row);
	});

	it('wear a colour when one is pressed', async () => {
		const row = colours(2);
		theme.follow({ [ACCENT_SWATCHES_KEY]: row });
		render();

		cards('Saved colors')[0].press();
		await tick();
		await tick();
		flushSync();

		expect([theme.accent, theme.accentHex]).toEqual(['custom', row[0]]);
		expect(cards('Saved colors').map((one) => one.chosen)).toEqual([true, false]);
	});

	it('paint each dot as it will be worn on this background, not as it was kept', () => {
		wearMidnight();
		const pale = colour(250, 240, 150);
		theme.follow({ [ACCENT_SWATCHES_KEY]: [pale] });
		render();

		const dot = host.querySelector<HTMLElement>('[aria-label="Saved colors"] .dot');
		const worn = wornAs(pale)!.accent;
		expect(worn).not.toBe(pale);
		expect(dot!.style.backgroundColor).toBe(asRgb(worn));
	});

	it('say in the label when a colour is worn differently, and how', () => {
		const kept = colour(250, 240, 150);
		expect(keptLabel(kept, null)).toBe(kept);
		expect(keptLabel(kept, 'darker')).toBe(
			`${kept}, worn darker on this background to stay legible`
		);
		expect(keptLabel(kept, 'lighter')).toContain('worn lighter');
		expect(keptLabel(kept, 'softer')).toContain('worn softer');
	});

	it('come out on Delete, with Undo', async () => {
		const row = colours(2);
		theme.follow({ [ACCENT_SWATCHES_KEY]: row });
		render();

		const first = host.querySelector<HTMLElement>('[aria-label="Saved colors"] [role="radio"]');
		first!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true }));
		await tick();
		await tick();
		flushSync();

		expect(theme.swatches).toEqual([row[1]]);
		expect(saveSettings).toHaveBeenLastCalledWith({ [ACCENT_SWATCHES_KEY]: [row[1]] });
	});

	/*
	 * A phone has no right button and a hold there is a selection, so its door is the three dots
	 * acting on what is picked: the colour in force, worn by pressing its dot.
	 */
	/** A row's words, less its glyph: an icon is a ligature, a private-use character in the text. */
	const named = (row: HTMLElement): string =>
		(row.textContent ?? '').replace(/[^\x20-\x7e]/g, '').trim();

	it('open their menu from the three dots on a phone, for the colour in force', async () => {
		const row = colours(3);
		theme.follow({ [ACCENT_SWATCHES_KEY]: row });
		await theme.set('accent', 'custom');
		await theme.set('accentHex', row[1]);
		saveSettings.mockClear();
		phoneWidth.yes = false;
		render();
		expect(
			host.querySelector(`button[aria-label="Saved color ${row[1]}"]`),
			'a second door on a desk, beside the right-click'
		).toBeNull();
		host.remove();

		phoneWidth.yes = true;
		try {
			render();
			const dots = host.querySelector<HTMLButtonElement>(
				`button[aria-label="Saved color ${row[1]}"]`
			);
			expect(dots, 'no door to a saved color on a phone').not.toBeNull();
			// The library opens on pointerdown; a click as well would open and shut it again.
			dots!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
			flushSync();
			let rows: HTMLElement[] = [];
			await vi.waitFor(() => {
				rows = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')];
				expect(rows.length, 'the menu never opened').toBeGreaterThan(0);
			});
			expect(rows.map(named)).toEqual(['Move left', 'Move right', 'Remove']);
			rows.find((one) => named(one) === 'Remove')!.click();
			await tick();
			await tick();
			flushSync();
			expect(theme.swatches).toEqual([row[0], row[2]]);
		} finally {
			phoneWidth.yes = false;
		}
	});

	it('refuse an eleventh with a sentence naming how to make room', async () => {
		theme.follow({ [ACCENT_SWATCHES_KEY]: colours(MAX_SWATCHES) });
		await theme.set('accent', 'custom');
		await theme.set('accentHex', colour(250, 240, 150));
		saveSettings.mockClear();
		render();

		// The picker is inside the Custom chip's popover.
		const custom = [...host.querySelectorAll<HTMLElement>('[role="radio"]')].find((one) =>
			one.textContent?.includes('Custom')
		);
		custom!.click();
		await tick();
		flushSync();
		const save = [...document.querySelectorAll<HTMLButtonElement>('button')].find((one) =>
			one.textContent?.includes('Save this color')
		);
		expect(save, 'no Save this color in the picker').toBeDefined();
		save!.click();
		await tick();
		await tick();
		flushSync();

		const said = document.querySelector('.keep-said')?.textContent ?? '';
		expect(said).toContain('Ten colors are saved');
		// One sentence for a desk and a phone: it names the act, never a pointer a phone lacks.
		expect(said).toContain('To make room, remove one from its menu.');
		expect(said).not.toMatch(/right-click/i);
		expect(saveSettings).not.toHaveBeenCalled();
		expect(theme.swatches).toHaveLength(MAX_SWATCHES);
	});
});
