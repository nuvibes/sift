/* The phone's leaf and bolt: the row at the top of More. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { imports } from '$lib/library/imports.svelte';
import TurboModeRow from './TurboModeRow.svelte';
import source from './TurboModeRow.svelte?raw';

const pressed = vi.hoisted(() => ({ asked: [] as boolean[] }));
vi.mock('./turbo-mode', async (original) => ({
	...(await original<typeof import('./turbo-mode')>()),
	pressTurboMode: vi.fn(async (on: boolean) => {
		pressed.asked.push(on);
	})
}));
vi.mock('$lib/shell/session.svelte', () => ({ session: { isAdmin: true } }));

let host: HTMLElement;
let drawn: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
	imports.page = null;
	pressed.asked = [];
});

function draw(facts: Record<string, unknown> | null): HTMLElement {
	imports.page = facts as typeof imports.page;
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(TurboModeRow, { target: host });
	flushSync();
	return host;
}

const working = { counts: { running: 3 }, stepping_back: true, step_back_for: 'input' };

describe('the row at the top of More', () => {
	it('draws nothing outside eco mode', () => {
		expect(draw({ counts: { running: 3 }, stepping_back: false }).textContent?.trim()).toBe('');
	});

	it('says eco mode for somebody working, green and undimmed, with the press for turbo mode', () => {
		const row = draw(working).querySelector('.turbo-mode-row');
		expect(row?.textContent).toContain(
			"In eco mode while you're working: using a quarter of this device"
		);
		expect(row?.textContent).toContain('Use turbo mode');
		expect(row?.classList.contains('dimmed')).toBe(false);
		row?.querySelector('button')?.click();
		expect(pressed.asked).toEqual([true]);
	});

	it('dims the leaf and says what other programs keep busy', () => {
		const row = draw({ ...working, step_back_for: 'others', step_back_over: ['graphics'] });
		const drawnRow = row.querySelector('.turbo-mode-row');
		expect(drawnRow?.classList.contains('dimmed')).toBe(true);
		expect(drawnRow?.textContent).toContain('other programs are using the GPU');
		const at = source.indexOf('.turbo-mode-row.dimmed > :global(');
		expect(source.slice(at, source.indexOf('}', at))).toContain(
			'opacity: var(--disabled-opacity);'
		);
	});

	it('offers eco mode again from the bolt', () => {
		const row = draw({ ...working, stepping_back: false, turbo_mode: true }).querySelector(
			'.turbo-mode-row'
		);
		expect(row?.classList.contains('full')).toBe(true);
		expect(row?.classList.contains('dimmed')).toBe(false);
		expect(row?.textContent).toContain('Use eco mode');
		row?.querySelector('button')?.click();
		expect(pressed.asked).toEqual([false]);
	});
});
