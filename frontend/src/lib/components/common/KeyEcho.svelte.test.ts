/*
 * The badge that says what a key just did.
 *
 * What is worth proving here is the rule the component is built on and nothing else: it reports a
 * PRESS, so the count it opened on says nothing, a press shows the state that press left behind, a
 * change of that state with no press behind it is silent, and it goes again on its own. The fade is
 * `arrive`'s and is proved where that lives.
 */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { IconName } from '$lib/design/icons';

import KeyEcho from './KeyEcho.svelte';

let instance: ReturnType<typeof mount> | null = null;
let host: HTMLElement;

beforeEach(() => {
	vi.useFakeTimers();
	host = document.createElement('div');
	document.body.appendChild(host);
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	host.remove();
	vi.useRealTimers();
});

function put(props: {
	icon: IconName;
	label: string;
	muted?: boolean;
	detail?: string;
	press?: number;
}) {
	const state = $state({ ...props });
	instance = mount(KeyEcho, { target: host, props: state });
	flushSync();
	return state;
}

/** The badge, or null while there is nothing to say. */
function badge(): HTMLElement | null {
	return host.querySelector('.key-echo');
}

describe('the echo of a key that changed something', () => {
	it('says nothing about the state it was drawn in', () => {
		// A player opening on "play through" has not just been set to play through. A badge over
		// every file as it opens is chrome.
		put({ icon: 'repeat', label: 'Play through', press: 0 });

		expect(badge()).toBeNull();
	});

	it('shows the state a press left behind, named for a screen reader', () => {
		const showing = put({ icon: 'repeat', label: 'Play through', press: 0 });

		showing.icon = 'repeat_one';
		showing.label = 'Repeat this';
		showing.press = 1;
		flushSync();

		expect(badge()?.getAttribute('aria-label')).toBe('Repeat this');
	});

	/*
	 * The badge is a key's echo, not a watcher of the setting: the same setting changed by the
	 * drawer's button is under the pointer and lit, and needs no badge in the far corner.
	 */
	it('stays quiet when the state changes with no press behind it', () => {
		const showing = put({ icon: 'repeat', label: 'Play through', press: 3 });

		showing.icon = 'repeat_one';
		showing.label = 'Repeat this';
		flushSync();

		expect(badge()).toBeNull();
	});

	/* A press that leaves the setting exactly where it was still says what it is: "nothing
	   happened" is the report somebody pressing a key in the dark is owed. A count is what can say
	   it; a state watched from in here never could. */
	it('echoes a press that changed nothing', () => {
		const showing = put({ icon: 'repeat', label: 'Play through', press: 1 });

		showing.press = 2;
		flushSync();

		expect(badge()?.getAttribute('aria-label')).toBe('Play through');
	});

	/*
	 * THE DETAIL, which is what makes this usable for the keys that move a NUMBER.
	 *
	 * A speaker glyph over the picture after pressing the volume key says the volume changed, which
	 * is the one thing anybody already knew. Both halves go in the name as well as on the badge:
	 * somebody who cannot see it pressed the same key and is owed the same answer, and the words are
	 * hidden from the reading of the region's contents so the number is not announced twice.
	 */
	it('says the number beside the glyph, and in the name', () => {
		const showing = put({ icon: 'volume_up', label: 'Volume', detail: '+2 (54)', press: 0 });

		showing.press = 1;
		flushSync();

		expect(badge()?.querySelector('.detail')?.textContent).toBe('+2 (54)');
		expect(badge()?.getAttribute('aria-label')).toBe('Volume +2 (54)');
	});

	it('is a glyph and nothing else when there is no number worth saying', () => {
		const showing = put({ icon: 'repeat', label: 'Play through', press: 0 });

		showing.press = 1;
		flushSync();

		expect(badge()?.querySelector('.detail')).toBeNull();
		expect(badge()?.classList.contains('worded')).toBe(false);
	});

	it('goes again on its own, without being told to', async () => {
		/* REAL time, for this one. The badge leaves on a transition, and a transition is driven by
		   animation frames rather than by the clock, so a faked clock takes the hold away and then
		   leaves the element standing mid-fade for ever, which reads as a badge that never goes. */
		vi.useRealTimers();
		const showing = put({ icon: 'repeat', label: 'Play through', press: 0 });
		showing.label = 'Repeat this';
		showing.icon = 'repeat_one';
		showing.press = 1;
		flushSync();
		expect(badge()).not.toBeNull();

		await vi.waitFor(
			() => {
				flushSync();
				if (badge()) throw new Error('still up');
			},
			{ timeout: 4000 }
		);
	});

	it('draws the off answer differently, because two answers share one glyph', () => {
		/* "Stop at the end" is the repeat arrows NOT lit, which is the rule the control in the
		   drawer already follows, so without this the badge would say the same thing for two
		   different answers. */
		const showing = put({ icon: 'repeat', label: 'Play through', press: 0 });

		showing.label = 'Stop at the end';
		showing.muted = true;
		showing.press = 1;
		flushSync();

		expect(badge()?.classList.contains('unlit')).toBe(true);
	});
});
