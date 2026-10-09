/* The settings shell: the list of sections, and the pane that shows one of them. */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import { reactiveProps } from '$lib/design/testing.svelte';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import shellSource from './SettingsShell.svelte?raw';

vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	fetchSettings: async () => []
}));
vi.mock('$lib/shell/session.svelte', () => ({ session: { isAdmin: true, isSignedIn: true } }));
vi.mock('$app/navigation', () => ({ goto: vi.fn(), afterNavigate: vi.fn() }));
vi.mock('$app/state', () => ({
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false },
	page: { url: new URL('http://localhost/settings/general'), state: {} }
}));

const opened = vi.hoisted(() => vi.fn());
vi.mock('$lib/settings-ui/settings-view', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings-view')>()),
	openSettings: opened
}));

const SettingsShell = (await import('./SettingsShell.svelte')).default;

let host: HTMLElement | undefined;
let drawn: Record<string, unknown> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
	removeStyles();
});

/** The pane's scrolling box, with a scroll position jsdom will actually keep. */
function paneBox(): { box: HTMLElement; top: () => number } {
	const box = [...(host?.querySelectorAll<HTMLElement>('.pane-slot *') ?? [])].find((element) =>
		[...element.attributes].some((attribute) => attribute.name.includes('viewport'))
	) as HTMLElement;
	let at = 0;
	Object.defineProperty(box, 'scrollTop', {
		configurable: true,
		get: () => at,
		set: (value: number) => (at = value)
	});
	return { box, top: () => at };
}

describe('Enter in the search box', () => {
	it('opens the first setting found and rings its row, the way pressing the result does', async () => {
		host = document.createElement('div');
		document.body.append(host);
		drawn = mount(SettingsShell, {
			target: host,
			props: {
				current: 'general',
				showingSection: true,
				children: createRawSnippet(() => ({ render: () => '<p>A section.</p>' }))
			}
		});
		await Promise.resolve();
		flushSync();
		const box = host.querySelector<HTMLInputElement>('.search-slot input')!;
		box.value = 'snapshot';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		flushSync();
		expect(opened).toHaveBeenLastCalledWith('backup', 'backup.now', undefined);
	});
});

describe('changing section', () => {
	it('opens the new section at its top', () => {
		const props = reactiveProps({
			current: 'general',
			showingSection: true,
			children: createRawSnippet(() => ({ render: () => '<p>A section.</p>' }))
		});
		host = document.createElement('div');
		document.body.append(host);
		drawn = mount(SettingsShell, { target: host, props });
		flushSync();

		const { box, top } = paneBox();
		expect(box, 'the pane has no scrolling box').toBeTruthy();
		box.scrollTop = 640;

		props.current = 'playback';
		flushSync();

		expect(top()).toBe(0);
	});
});

/* THE SECTION'S TITLE AND THE SEARCH BOX START ON ONE LINE, at the desktop width. */
describe('the pane heading and the search box', () => {
	/** Every style rule in the shell's sheet, with the media condition it sits under, if any. */
	function rules(): { media: string; selector: string; style: CSSStyleDeclaration }[] {
		const found: { media: string; selector: string; style: CSSStyleDeclaration }[] = [];
		for (const sheet of [...document.styleSheets]) {
			for (const rule of [...sheet.cssRules]) {
				if (rule instanceof CSSMediaRule) {
					for (const inner of [...rule.cssRules]) {
						if (inner instanceof CSSStyleRule)
							found.push({
								media: rule.media.mediaText,
								selector: inner.selectorText,
								style: inner.style
							});
					}
				} else if (rule instanceof CSSStyleRule) {
					found.push({ media: '', selector: rule.selectorText, style: rule.style });
				}
			}
		}
		return found;
	}

	function declared(media: string, selector: RegExp, property: string): string {
		const hit = rules().find(
			(one) =>
				(media === '' ? one.media === '' : one.media.includes(media)) &&
				selector.test(one.selector) &&
				one.style.getPropertyValue(property) !== ''
		);
		expect(hit, `no ${property} for ${selector} under "${media}"`).toBeDefined();
		return hit!.style.getPropertyValue(property).trim();
	}

	it('puts the pane on the search row, inset from its top by the same token', () => {
		applyStyles(shellSource);

		const areas = declared('', /^\.settings\.svelte-[a-z0-9]+$/, 'grid-template-areas');
		const rows = areas.match(/"[^"]*"|'[^']*'/g)?.map((row) => row.slice(1, -1).trim()) ?? [];
		const searchRow = rows.findIndex((row) => row.split(/\s+/)[0] === 'search');
		expect(searchRow, `no search row in ${areas}`).toBeGreaterThan(-1);
		expect(rows[searchRow]?.split(/\s+/)[1], 'the pane does not start on the search row').toBe(
			'pane'
		);

		const desktop = 'min-width: 768px';
		const searchTop = declared(desktop, /\.search-slot/, 'padding-block-start');
		const paneTop = declared(desktop, /\.pane\.svelte/, 'padding-block');
		expect(searchTop).toBe(paneTop);
	});

	/* A setting found under a section starts its words on the left edge of the section's icon: the
	   two rows share one box edge and one inset, and nothing indents the list under the section. */
	it('starts a found setting on the left edge of its section icon', () => {
		applyStyles(shellSource);

		expect(declared('', /\.results\.svelte-[a-z0-9]+ \.under/, 'padding-inline-start')).toBe('0px');
		expect(declared('', /^nav\.svelte-[a-z0-9]+ \.item$/, 'padding')).toBe(
			'var(--space-2) var(--space-3)'
		);
	});
});

describe('a settings path pasted into the search box', () => {
	it('goes straight to the row it names, as pressing its result would', async () => {
		host = document.createElement('div');
		document.body.append(host);
		drawn = mount(SettingsShell, {
			target: host,
			props: {
				current: 'general',
				showingSection: true,
				children: createRawSnippet(() => ({ render: () => '<p>A section.</p>' }))
			}
		});
		await Promise.resolve();
		flushSync();
		const box = host.querySelector<HTMLInputElement>('.search-slot input')!;
		const paste = new Event('paste', { bubbles: true, cancelable: true });
		Object.defineProperty(paste, 'clipboardData', {
			value: { getData: () => 'Settings > Backup and restore > Save a backup now > Save a backup' }
		});
		box.dispatchEvent(paste);
		flushSync();
		expect(paste.defaultPrevented).toBe(true);
		expect(opened).toHaveBeenLastCalledWith('backup', 'backup.now', undefined);
	});

	it('leaves an ordinary paste to the box', async () => {
		opened.mockClear();
		host = document.createElement('div');
		document.body.append(host);
		drawn = mount(SettingsShell, {
			target: host,
			props: {
				current: 'general',
				showingSection: true,
				children: createRawSnippet(() => ({ render: () => '<p>A section.</p>' }))
			}
		});
		await Promise.resolve();
		flushSync();
		const box = host.querySelector<HTMLInputElement>('.search-slot input')!;
		const paste = new Event('paste', { bubbles: true, cancelable: true });
		Object.defineProperty(paste, 'clipboardData', { value: { getData: () => 'snapshot' } });
		box.dispatchEvent(paste);
		flushSync();
		expect(paste.defaultPrevented).toBe(false);
		expect(opened).not.toHaveBeenCalled();
	});
});
