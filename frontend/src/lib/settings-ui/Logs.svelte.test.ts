/* Tasks and Activity > Logs hands the desktop shell's own log the library's Detail and Hide
 * personal details in the log: once when the pane reads them, again whenever either is saved or
 * read back changed, and never twice for one pair of answers. */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import type { SettingSection } from '$lib/settings-ui/settings';
import { settingChanges } from '$lib/library/changes.svelte';

const shell = vi.hoisted(() => ({
	detail: vi.fn(async (detailed: boolean, _hidePersonal: boolean) => detailed)
}));
const fetchSettings = vi.hoisted(() => vi.fn<() => Promise<SettingSection[]>>());
const saveSettings = vi.hoisted(() => vi.fn(async () => undefined));

vi.mock('$lib/api/client', () => ({
	ApiError: class extends Error {},
	api: {
		get: vi.fn(async () => ({
			lines: [],
			path: 'sift.log',
			size_bytes: 0,
			present: true,
			searched_bytes: 0,
			whole: true
		}))
	}
}));

vi.mock('$lib/bridge', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/bridge')>();
	return {
		...real,
		bridge: {
			...real.bridge,
			canReadShellLog: () => false,
			shellLog: vi.fn(async () => null),
			shellLogDetail: shell.detail
		}
	};
});

vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	fetchSettings: () => fetchSettings(),
	saveSettings: (...args: unknown[]) => saveSettings(...(args as [])),
	onSettingsSaved: vi.fn()
}));

import Logs from './Logs.svelte';
import { forgetToldShellLogDetail } from '$lib/desktop/shell-log-detail';

/* jsdom has no pointer capture, and the select primitive releases it on the way down. */
for (const name of ['setPointerCapture', 'releasePointerCapture', 'hasPointerCapture'] as const) {
	if (!(name in Element.prototype)) {
		Object.defineProperty(Element.prototype, name, { value: () => false, writable: true });
	}
}

function sections(detail: string, hide = false): SettingSection[] {
	return [
		{
			name: 'Logs',
			settings: [
				{
					key: 'logs.detail',
					label: 'Log detail',
					help: 'Normal records what happened.',
					value: detail,
					default: 'normal',
					scope: 'app',
					choices: ['normal', 'detailed'],
					choice_labels: ['Normal', 'Detailed']
				},
				{
					key: 'logs.hide_personal',
					label: 'Hide personal details in the log',
					help: 'Hides the names in file paths.',
					value: hide,
					default: false,
					scope: 'app'
				}
			]
		}
	] as unknown as SettingSection[];
}

let host: HTMLElement;
let shown: ReturnType<typeof mount> | null = null;

beforeEach(() => {
	forgetToldShellLogDetail();
	shell.detail.mockClear();
	saveSettings.mockClear();
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (shown) unmount(shown);
	shown = null;
	host.remove();
	document.body.innerHTML = '';
});

async function draw(detail: string) {
	fetchSettings.mockResolvedValue(sections(detail));
	shown = mount(Logs, { target: host });
	flushSync();
	await vi.waitFor(() => expect(host.textContent).toContain('Log detail'));
}

function press(element: Element | null | undefined) {
	element?.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
	element?.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0 }));
	(element as HTMLElement | null | undefined)?.click();
	flushSync();
}

it('tells the shell the Detail it reads, once, and again when it is read back changed', async () => {
	await draw('detailed');
	await vi.waitFor(() => expect(shell.detail).toHaveBeenCalledWith(true, false));
	expect(shell.detail).toHaveBeenCalledTimes(1);

	// Read again unchanged (a setting moved somewhere else): the shell already knows.
	settingChanges.changed();
	await vi.waitFor(() => expect(fetchSettings).toHaveBeenCalledTimes(2));
	await tick();
	expect(shell.detail).toHaveBeenCalledTimes(1);

	fetchSettings.mockResolvedValue(sections('normal'));
	settingChanges.changed();
	await vi.waitFor(() => expect(shell.detail).toHaveBeenLastCalledWith(false, false));
	expect(shell.detail).toHaveBeenCalledTimes(2);
});

it('tells the shell the Detail it saves', async () => {
	fetchSettings.mockClear();
	await draw('normal');
	await vi.waitFor(() => expect(shell.detail).toHaveBeenCalledWith(false, false));

	const select = [...host.querySelectorAll('.ui-select')].find((one) =>
		one.textContent?.includes('Normal')
	);
	press(select);
	const item = [...document.querySelectorAll('.ui-select-item-label')].find(
		(one) => one.textContent?.trim() === 'Detailed'
	);
	expect(item, 'no choice labelled Detailed').toBeTruthy();
	press(item?.closest('.ui-select-item'));
	await tick();

	await vi.waitFor(() => expect(saveSettings).toHaveBeenCalledWith({ 'logs.detail': 'detailed' }));
	expect(shell.detail).toHaveBeenLastCalledWith(true, false);
	expect(shell.detail).toHaveBeenCalledTimes(2);
});

it('draws Hide personal details in the log on the page, and tells the shell when it is saved', async () => {
	await draw('normal');
	await vi.waitFor(() => expect(shell.detail).toHaveBeenCalledWith(false, false));
	expect(host.textContent).toContain('Hide personal details in the log');

	const toggle = host.querySelector('[id="logs.hide_personal-control"]');
	expect(toggle, 'the switch').toBeTruthy();
	press(toggle);
	await tick();

	await vi.waitFor(() => expect(saveSettings).toHaveBeenCalledWith({ 'logs.hide_personal': true }));
	expect(shell.detail).toHaveBeenLastCalledWith(false, true);
});
