import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import SettingRow from './SettingRow.svelte';
import type { SettingEntry } from '$lib/settings-ui/settings';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import source from './SettingRow.svelte?raw';

/* A menu whose choices are NUMBERS hands back a number. */

let showing: Record<string, unknown> | null = null;
let host: HTMLElement;

/* jsdom has no pointer capture; see `Select.svelte.test.ts` for why the menu needs it stubbed. */
for (const name of ['setPointerCapture', 'releasePointerCapture', 'hasPointerCapture'] as const) {
	if (!(name in Element.prototype)) {
		Object.defineProperty(Element.prototype, name, { value: () => false, writable: true });
	}
}

function entry(overrides: Partial<SettingEntry>): SettingEntry {
	return {
		key: 'playback.max_transcode_height',
		label: 'Highest quality when converting',
		help: 'The tallest picture a converted copy is made at.',
		...overrides
	} as SettingEntry;
}

function show(one: SettingEntry, onchange: (value: unknown) => void) {
	host = document.createElement('div');
	document.body.append(host);
	showing = mount(SettingRow, {
		target: host,
		props: { entry: one, value: one.default, onchange }
	});
	flushSync();
}

function pick(label: string) {
	const trigger = host.querySelector<HTMLElement>('.ui-select');
	for (const type of ['pointerdown', 'pointerup'])
		trigger?.dispatchEvent(new MouseEvent(type, { bubbles: true, button: 0 }));
	trigger?.click();
	flushSync();
	const item = [...document.querySelectorAll<HTMLElement>('.ui-select-item')].find(
		(one) => one.textContent?.trim() === label
	);
	expect(item, `no option labelled ${label}`).toBeTruthy();
	for (const type of ['pointerdown', 'pointerup'])
		item?.dispatchEvent(new MouseEvent(type, { bubbles: true, button: 0 }));
	item?.click();
	flushSync();
}

afterEach(() => {
	if (showing) unmount(showing);
	showing = null;
	host?.remove();
	document.body.innerHTML = '';
});

describe('a menu over declared choices', () => {
	it('hands back a NUMBER for a number choice, the type the server checks against', () => {
		const told: unknown[] = [];
		show(
			entry({ default: 1080, choices: [480, 720, 1080], choice_labels: ['480p', '720p', '1080p'] }),
			(value) => told.push(value)
		);
		pick('720p');

		expect(told).toEqual([720]);
	});

	it('still hands back a string for a string choice', () => {
		const told: unknown[] = [];
		show(
			entry({
				key: 'playback.something',
				default: 'cpu',
				choices: ['cpu', 'nvidia'],
				choice_labels: ['CPU', 'GPU']
			}),
			(value) => told.push(value)
		);
		pick('GPU');

		expect(told).toEqual(['nvidia']);
	});
});

describe("a menu's width on a row", () => {
	afterEach(removeStyles);

	it("is its own, packed to the column's far edge, never stretched across the column", () => {
		show(
			entry({ default: 1080, choices: [480, 720, 1080], choice_labels: ['480p', '720p', '1080p'] }),
			() => {}
		);
		const chooser = host.querySelector<HTMLElement>('.chooser')!;
		applyStyles(source, chooser);

		const style = getComputedStyle(chooser);
		expect(style.width).not.toBe('100%');
		// Where the row packs: the far edge on a desktop window, the start once the row stacks on a
		// phone (`--row-pack`, published by `LabelledRow`; `row-pack.test.ts` holds the flip).
		expect(style.justifyContent).toBe('var(--row-pack, flex-end)');
		expect(style.maxInlineSize).toBe('100%');
		expect(getComputedStyle(host.querySelector('.ui-select')!).inlineSize).not.toBe('100%');
	});
});

describe("a number's box on a row", () => {
	it('is sized by its own maximum and its word for zero, with no empty unit cell', () => {
		show(
			entry({
				key: 'download.skip_smaller_mb',
				label: 'Skip files smaller than',
				default: 0,
				minimum: 0,
				maximum: 100_000,
				unit: 'MB',
				automatic_label: 'No minimum'
			}),
			() => {}
		);
		const words = [...host.querySelectorAll<HTMLElement>('.sizer')].map((one) => one.dataset.words);
		expect(words).toEqual(['0000000', 'No minimum']);
		expect(host.querySelector('input')?.value).toBe('No minimum');
	});

	it('keeps no room for a unit a count does not have', () => {
		show(
			entry({ key: 'backup.keep', label: 'Backups to keep', default: 7, minimum: 1, maximum: 60 }),
			() => {}
		);
		expect(host.querySelector('.unit')).toBeNull();
		expect(host.querySelector('.number')?.classList.contains('trailered')).toBe(false);
		expect(
			[...host.querySelectorAll<HTMLElement>('.sizer')].map((one) => one.dataset.words)
		).toEqual(['000']);
	});
});
