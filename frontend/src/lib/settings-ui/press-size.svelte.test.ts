/*
 * One press height per pane, decided by what holds the press: a settings row's press and a press in
 * a pane's form are the small control, a press beside a field in the same control column takes the
 * field's height, and a form's answers and a dialog's presses are the default size.
 */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, expect, it } from 'vitest';

import Probe from './PressSizeProbe.test.svelte';

let drawn: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	document.body.innerHTML = '';
});

function sizeOf(press: string): string {
	const button = document.querySelector(`[data-press="${press}"]`);
	expect(button, `no press ${press}`).not.toBeNull();
	return button!.classList.contains('small') ? 'small' : 'medium';
}

it('draws each press at the size its holder gives it', () => {
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Probe, { target: host });
	flushSync();

	expect(sizeOf('row')).toBe('small');
	expect(sizeOf('named'), 'a size the caller names wins').toBe('medium');
	expect(sizeOf('row-submit'), 'a submit is the default size wherever it stands').toBe('medium');
	expect(sizeOf('form-body')).toBe('small');
	expect(sizeOf('cancel'), "a form's answers stand level with its submit").toBe('medium');
	expect(sizeOf('submit')).toBe('medium');
	expect(sizeOf('opener')).toBe('small');
	expect(sizeOf('dialog'), 'a dialog starts again').toBe('medium');
	expect(sizeOf('alone')).toBe('medium');

	const chooser = [...host.querySelectorAll('button')].find(
		(one) => one.textContent?.trim() === 'Choose a file'
	);
	expect(chooser?.classList.contains('small'), 'the file chooser in a row').toBe(true);

	const beside = [...host.querySelectorAll('.split button')];
	expect(beside.length).toBeGreaterThan(0);
	for (const half of beside) {
		expect(half.classList.contains('medium'), 'a press beside a select takes its height').toBe(
			true
		);
	}
});
