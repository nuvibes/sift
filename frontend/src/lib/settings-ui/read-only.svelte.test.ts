/*
 * The panes about the computer Sift runs on, at a phone's width: read only. Every row still says
 * what it is set to; nothing on the pane changes it. A row's control stands in a disabled fieldset,
 * which stops every button, box and switch inside answering whatever it is made of, and a row whose
 * control is a press draws no press.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { phoneWidth } from '$lib/components/common/phone-width.svelte';
import HeldPaneProbe from './HeldPaneProbe.test.svelte';
import { heldHere, READ_ONLY_ON_A_PHONE } from './read-only';

let drawn: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	phoneWidth.yes = false;
	document.body.innerHTML = '';
});

function pane(section: string): HTMLElement {
	drawn = mount(HeldPaneProbe, { target: document.body, props: { section } });
	flushSync();
	return document.body;
}

describe('a pane about the computer Sift runs on', () => {
	it('is Performance, Backup and Maintenance, and nothing else', () => {
		expect([...READ_ONLY_ON_A_PHONE].sort()).toEqual(['backup', 'maintenance', 'performance']);
	});

	it('is held only at a phone width', () => {
		expect(heldHere('backup')).toBe(false);
		phoneWidth.yes = true;
		expect(heldHere('backup')).toBe(true);
		expect(heldHere('appearance')).toBe(false);
	});

	it('draws its controls disabled and its presses not at all on a phone', () => {
		phoneWidth.yes = true;
		const body = pane('maintenance');
		const control = body.querySelector<HTMLButtonElement>('.control button')!;
		expect(control.closest('fieldset')?.disabled).toBe(true);
		expect(control.matches(':disabled')).toBe(true);
		expect(body.textContent).toContain('Optimize the database');
		// The row's figure still reads; only its press is gone.
		expect(body.textContent).toContain('120 MB to free');
		expect(body.querySelector('.press')).toBeNull();
	});

	it('leaves every control live on a desktop window, and on a phone for any other pane', () => {
		let body = pane('maintenance');
		expect(body.querySelector('fieldset')).toBeNull();
		expect(body.querySelector('.press button')).not.toBeNull();
		unmount(drawn!);
		phoneWidth.yes = true;
		body = pane('appearance');
		expect(body.querySelector('fieldset')).toBeNull();
		expect(body.querySelector('.press button')).not.toBeNull();
	});
});
