/*
 * The window's own strip: back and forward, drawn only in the desktop application.
 *
 * A browser keeps its own arrows, on a desktop and on a phone, so the strip draws none there and
 * listens to nothing. In the application the two go where the browser's would (the window's
 * history), each dimmed where there is nowhere to go, and read again when the entry moves.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import WindowBar from './WindowBar.svelte';
import source from './WindowBar.svelte?raw';
import { rail } from './rail-state.svelte';
import { session } from '$lib/shell/session.svelte';
import { updates, type UpdateState } from '$lib/shell/updates.svelte';

const opened = vi.hoisted(() => ({ sections: [] as string[] }));
vi.mock('$lib/settings-ui/settings-view', () => ({
	openSettings: (section: string, key?: string) =>
		opened.sections.push(key ? `${section} ${key}` : section)
}));

/* The token sheet, read off the disk: a stylesheet asked for through `?raw` arrives empty here. */
const appCss = readFileSync(resolve('src/app.css'), 'utf8');

class FakeHistory extends EventTarget {
	canGoBack = false;
	canGoForward = false;

	moveTo(back: boolean, forward: boolean): void {
		this.canGoBack = back;
		this.canGoForward = forward;
		this.dispatchEvent(new Event('currententrychange'));
	}
}

let host: HTMLElement;
let drawn: ReturnType<typeof mount> | null = null;
let entries: FakeHistory;

function draw(arrows: boolean): void {
	drawn = mount(WindowBar, { target: host, props: { arrows } });
	flushSync();
}

function arrow(name: string): HTMLButtonElement | null {
	return host.querySelector<HTMLButtonElement>(`button[aria-label="${name}"]`);
}

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
	entries = new FakeHistory();
	Object.defineProperty(window, 'navigation', { value: entries, configurable: true });
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host.remove();
	delete window.sift;
	Reflect.deleteProperty(window, 'navigation');
	vi.restoreAllMocks();
	updates.state = null;
	opened.sections = [];
});

describe('the strip itself', () => {
	/* The same ground as the rail below it, and the caption buttons the operating system draws
	   beside it have none, so a rule along its foot would run across the rail and stop short of them.
	   Read from the stylesheet: the strip is drawn only under the attribute the shell stamps. */
	it('draws no line along its foot', () => {
		const rule = source.slice(source.indexOf('.window-bar {\n\t\tdisplay: flex;'));
		const body = rule.slice(0, rule.indexOf('}'));
		expect(body, 'the strip rule moved').toContain('app-region: drag');
		expect(body).not.toMatch(/border/);
	});
});

describe('where the arrows stand', () => {
	/* The drawn back arrow's left edge on the mark's left edge, in both of the rail's widths:
	   the arrow's ink and the mark's ink start on the same pixel column, 20px in with labels and
	   17px with icons only. Read from the stylesheet here,
	   which is where the two insets are, and from the markup for which one applies. */
	function rule(selector: string): string {
		const at = source.indexOf(`${selector} {`);
		expect(at, `${selector} moved`).toBeGreaterThan(-1);
		const body = source.slice(at);
		return body.slice(0, body.indexOf('}'));
	}

	it('start the drawn arrow on the mark, whichever mark the rail is drawing', () => {
		const open = rule(":global(:root[data-window='overlaid']) .arrows");
		const tight = rule(":global(:root[data-window='overlaid']) .arrows.tight");
		// The button's padding (7px) and the glyph's own side bearing (4px).
		expect(open).toContain('--arrow-lead: 11px;');
		// The lockup: the rail body's padding and the brand's.
		expect(open).toContain(
			'inset-inline-start: calc(var(--space-3) + var(--space-2) - var(--arrow-lead));'
		);
	});

	/* With icons only, Back and Forward both inside the collapsed rail: the pair spans the rail and
	   is centred on it, each press half the rail less a small gap, so Forward does not hang past
	   the rail over the page's corner. Measured from the tokens the rules read. */
	it('keep both arrows inside the collapsed rail', () => {
		const tight = rule(":global(:root[data-window='overlaid']) .arrows.tight");
		const press = rule(":global(:root[data-window='overlaid']) .arrows.tight :global(.btn)");
		expect(tight).toContain('inset-inline-start: 0;');
		expect(tight).toContain('inline-size: var(--rail-width-collapsed);');
		expect(tight).toContain('justify-content: center;');
		expect(tight).toContain('gap: 0;');
		expect(press).toContain(
			'--arrow-tight: calc(var(--rail-width-collapsed) / 2 - var(--space-1));'
		);
		expect(press).toContain('inline-size: var(--arrow-tight);');

		const token = (name: string) => {
			const found = appCss.match(new RegExp(`${name}:\\s*(\\d+)px;`));
			expect(found, `${name} moved`).not.toBeNull();
			return Number(found![1]);
		};
		const rail = token('--rail-width-collapsed');
		const each = rail / 2 - token('--space-1');
		const start = (rail - 2 * each) / 2;
		expect(start).toBeGreaterThanOrEqual(0);
		expect(start + 2 * each, 'Forward ends past the rail').toBeLessThanOrEqual(rail);
		// The glyph (18px) still fits its press.
		expect(each).toBeGreaterThanOrEqual(18);
	});

	it('stand lower than the centre by 15 percent of where the arrow was drawn', () => {
		expect(rule(":global(:root[data-window='overlaid']) .arrows")).toContain('translate: 0 2px;');
	});

	it('follow the rail from labels to icons only and back', () => {
		window.sift = { isDesktop: true, setTitleBar: async () => true };
		const was = rail.collapsed;
		rail.collapsed = false;
		draw(true);
		const group = host.querySelector('.arrows');
		expect(group?.classList.contains('tight')).toBe(rail.narrow);
		rail.collapsed = true;
		flushSync();
		expect(group?.classList.contains('tight')).toBe(true);
		rail.collapsed = was;
	});
});

describe('the back and forward arrows', () => {
	it('are not drawn in a browser, so its own arrows are the only ones', () => {
		draw(true);
		expect(arrow('Back')).toBeNull();
		expect(arrow('Forward')).toBeNull();
	});

	it('are not drawn in the application outside its own frame', () => {
		window.sift = { isDesktop: true, setTitleBar: async () => true };
		draw(false);
		expect(arrow('Back')).toBeNull();
	});

	it('go where the browser would, each dimmed with nowhere to go', () => {
		window.sift = { isDesktop: true, setTitleBar: async () => true };
		const back = vi.spyOn(history, 'back').mockImplementation(() => {});
		const forward = vi.spyOn(history, 'forward').mockImplementation(() => {});
		draw(true);

		expect(arrow('Back')?.disabled, 'the first screen has nothing behind it').toBe(true);
		expect(arrow('Forward')?.disabled).toBe(true);

		entries.moveTo(true, false);
		flushSync();
		expect(arrow('Back')?.disabled).toBe(false);
		expect(arrow('Forward')?.disabled).toBe(true);
		arrow('Back')?.click();
		expect(back).toHaveBeenCalledTimes(1);

		entries.moveTo(false, true);
		flushSync();
		expect(arrow('Back')?.disabled).toBe(true);
		arrow('Forward')?.click();
		expect(forward).toHaveBeenCalledTimes(1);
	});
});

describe('an update waiting', () => {
	function offer(dismissed: boolean): void {
		updates.state = {
			current_version: '0.2.0',
			latest_version: '0.2.1',
			update_available: true,
			dismissed
		} as UpdateState;
	}

	function inTheApp(admin = true): void {
		window.sift = { isDesktop: true, setTitleBar: async () => true };
		vi.spyOn(session, 'isAdmin', 'get').mockReturnValue(admin);
	}

	const press = () =>
		host.querySelector<HTMLButtonElement>(
			'.update button[aria-label^="Open "][aria-label$=" Updates and Info: Sift 0.2.1 is available"]'
		);

	it('is a press opening Updates and Info, kept after the banner was dismissed', () => {
		inTheApp();
		offer(true);
		draw(true);
		expect(press()).not.toBeNull();
		press()?.click();
		expect(opened.sections).toEqual(['updates updates.version']);
	});

	it('is not drawn in a browser, outside the frame, for a guest, or with nothing newer', () => {
		offer(false);
		vi.spyOn(session, 'isAdmin', 'get').mockReturnValue(true);
		draw(true);
		expect(press(), 'a browser').toBeNull();
		unmount(drawn!);

		inTheApp();
		draw(false);
		expect(press(), 'the sign-in and setup screens').toBeNull();
		unmount(drawn!);

		inTheApp(false);
		draw(true);
		expect(press(), 'a guest').toBeNull();
		unmount(drawn!);

		inTheApp();
		updates.state = { ...updates.state!, update_available: false };
		draw(true);
		expect(press(), 'nothing newer').toBeNull();
	});

	it('sits left of the system buttons, out of the drag region', () => {
		const at = source.indexOf(":global(:root[data-window='overlaid']) .update {");
		expect(at, 'the rule moved').toBeGreaterThan(-1);
		const body = source.slice(at, source.indexOf('}', at));
		expect(body).toContain('inset-inline-end: var(--captions);');
		expect(body).toContain('app-region: no-drag;');
		expect(source).toContain('inline-size: var(--captions);');
	});
});
