/* The Playback pane, and Theater's rows on it.
 *
 * Theater is not a section: its rows are a group of their own on this pane, under their own
 * heading, and an address naming Theater lands here (see `MOVED_TO`). What a test here can say:
 * the group is drawn under its heading with every one of Theater's rows (including "When a
 * preview comes up", registered into Theater, which a search result must open a pane containing),
 * and a pane whose settings will not load says so.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { SettingSection } from '$lib/settings-ui/settings';
const remote = vi.hoisted(() => ({ app: false, thisBrowser: false, set: vi.fn() }));

vi.mock('$lib/bridge', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/bridge')>();
	return { ...real, bridge: { ...real.bridge, isDesktop: () => remote.app } };
});

vi.mock('$lib/remote/offer.svelte', () => ({
	screenOffer: {
		get thisBrowser() {
			return remote.thisBrowser;
		},
		setThisBrowser: remote.set
	}
}));

import Playback from './Playback.svelte';
import { COPY } from './Playback.search';

const fetchSettings = vi.fn<() => Promise<SettingSection[]>>();

vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	fetchSettings: () => fetchSettings(),
	saveSettings: vi.fn(),
	onSettingsSaved: vi.fn()
}));

vi.mock('$lib/shell/interface-state.svelte', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/shell/interface-state.svelte')>()),
	recallInterfaceState: vi.fn(async () => undefined),
	popoutLeavesToMini: vi.fn(() => true),
	rememberPopoutLeavesToMini: vi.fn()
}));

function setting(key: string, label: string, extra: Record<string, unknown> = {}) {
	return { key, label, help: `${label}.`, value: false, default: false, scope: 'user', ...extra };
}

const THEATER_KEYS = [
	'theater.layout',
	'theater.center_stage',
	'theater.autoplay',
	'theater.timer_seconds',
	'theater.resume'
];

const SECTIONS = [
	{ name: 'Playback', settings: [setting('playback.resume_enabled', 'Remember where you were')] },
	{
		name: 'Theater',
		settings: [
			setting('theater.layout', 'Default layout when Theater opens'),
			setting('theater.center_stage', 'When a preview comes up'),
			setting('theater.autoplay', 'Start playing when Theater opens'),
			setting('theater.timer_seconds', 'Play the next file after'),
			setting('theater.resume', 'Pick up where you left off')
		]
	}
] as unknown as SettingSection[];

let host: HTMLDivElement;
let mounted: Record<string, unknown> | null = null;

async function draw() {
	mounted = mount(Playback, { target: host }) as Record<string, unknown>;
	flushSync();
	await vi.waitFor(() => expect(host.querySelector('.stack')).toBeNull());
	flushSync();
}

beforeEach(() => {
	remote.app = false;
	remote.thisBrowser = false;
	remote.set.mockReset();
	host = document.createElement('div');
	document.body.append(host);
	fetchSettings.mockReset();
});

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host.remove();
});

it("draws Theater's rows as a group of their own, under Theater's heading", async () => {
	fetchSettings.mockResolvedValue(SECTIONS);

	await draw();

	const headings = [...host.querySelectorAll('h2, h3, [role="heading"]')].map((one) =>
		one.textContent?.trim()
	);
	expect(headings).toContain(COPY.theater);
	for (const key of THEATER_KEYS) {
		expect(host.querySelector(`[id="${key}"]`), `${key} has a row a link can ring`).not.toBeNull();
	}
});

it('says the settings could not be loaded rather than drawing an empty screen', async () => {
	fetchSettings.mockRejectedValue(new Error('no'));

	await draw();

	expect(host.textContent).toContain(COPY.cannotLoad);
});

/* The phone as a remote: a browser tab offers what it plays only once switched on here, and the
   app always does, so there the switch stands on and still, saying why. */
function remoteSwitch(): HTMLElement {
	const row = host.querySelector('[id="playback.remote.this_browser"]');
	const control = row?.querySelector<HTMLElement>('[role="switch"]');
	if (!control) throw new Error('no remote switch on the Playback pane');
	return control;
}

it("draws this browser's remote switch, off until turned on, and turns it on", async () => {
	fetchSettings.mockResolvedValue(SECTIONS);

	await draw();
	const control = remoteSwitch();
	expect(control.getAttribute('aria-checked')).toBe('false');
	control.click();
	flushSync();

	expect(remote.set).toHaveBeenCalledWith(true);
	expect(host.textContent).toContain(COPY.thisBrowser.help);
});

it("stands the remote switch on and still in Sift's app, which is always offered", async () => {
	remote.app = true;
	fetchSettings.mockResolvedValue(SECTIONS);

	await draw();
	const control = remoteSwitch();

	expect(control.getAttribute('aria-checked')).toBe('true');
	expect(control.hasAttribute('disabled') || control.getAttribute('aria-disabled') === 'true').toBe(
		true
	);
	expect(host.textContent).toContain(COPY.thisBrowser.helpApp);
});

it("draws the default layout's shapes as the wall's own Layouts chooser does", async () => {
	/* The same choice as the Layouts menu on the wall, so the same pictures: a list of "1x3" and
	   "Center stage 2x2" in words beside it would be the same question asked two ways. */
	const layout = setting('theater.layout', 'Default layout when Theater opens', {
		value: 'grid',
		default: 'side_by_side_by_side',
		choices: ['single', 'grid'],
		choice_labels: ['1x1', '2x2']
	});
	fetchSettings.mockResolvedValue([
		{ name: 'Theater', settings: [layout] }
	] as unknown as SettingSection[]);

	await draw();

	const row = host.querySelector('[id="theater.layout"]') as HTMLElement;
	const picture = row.querySelector('.ui-select-item-preview .glyph');
	expect(picture, 'the chosen layout is not drawn as its shape').not.toBeNull();
	expect(picture?.querySelectorAll('.block')).toHaveLength(4);
});

it('says Portrait and Landscape on the default layout chooser, from the one place they are named', async () => {
	for (const name of ['setPointerCapture', 'releasePointerCapture', 'hasPointerCapture'] as const) {
		if (!(name in Element.prototype))
			Object.defineProperty(Element.prototype, name, { value: () => false, writable: true });
	}
	const layout = setting('theater.layout', 'Default layout when Theater opens', {
		value: 'single',
		default: 'side_by_side_by_side',
		choices: ['single', 'side_by_side', 'stacked'],
		choice_labels: ['1x1', '1x2 (P)', '1x2 (L)']
	});
	fetchSettings.mockResolvedValue([
		{ name: 'Theater', settings: [layout] }
	] as unknown as SettingSection[]);
	await draw();

	const trigger = host.querySelector<HTMLElement>('[id="theater.layout"] .ui-select')!;
	trigger.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
	trigger.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0 }));
	trigger.click();
	flushSync();
	const rows = [...document.querySelectorAll<HTMLElement>('.ui-select-item')];
	expect(rows.map((one) => one.querySelector('.wrap') !== null)).toEqual([false, true, true]);

	rows[2].querySelector('.wrap')!.dispatchEvent(new PointerEvent('pointermove', { bubbles: true }));
	await vi.waitFor(() => {
		flushSync();
		expect(document.querySelector('[role="tooltip"]')?.textContent?.trim()).toBe('Landscape');
	});
});
