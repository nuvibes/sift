/* What happens at the end, on a picture.
 *
 * A run set to Play through moves off a picture after its rest, so the answer moving the run has to
 * be changeable from the screen it is moving, not drawn dimmed there ("A picture has no end to
 * repeat"). The answer is one, held where every player reads it
 * (`dwell.mode`), and a picture moves a run on by the same rule a clip's end does
 * (`run.movesOnAfter`): Repeat this and Stop at the end stay on it, Play through rests and moves on.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import StillHarness from './StillHarness.svelte';
import StillView from './StillView.svelte';
import { dwell, PICTURE_SECONDS } from '$lib/player/dwell.svelte';
import { run } from '$lib/player/run.svelte';
import { mini } from '$lib/player/mini.svelte';
import { saveSettings } from '$lib/settings-ui/settings';
import type { LoopMode } from '$lib/player/loop-modes';
import { keyOf } from '$lib/player/acts';

vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	fetchSettingValues: vi.fn(async () => new Map<string, unknown>()),
	saveSettings: vi.fn(async () => {})
}));

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

function takeDown() {
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
}

/** The account's answers, as if they had arrived: photographs included, and `mode` at the end. */
function answers(mode: LoopMode) {
	dwell.known = true;
	dwell.pictures = true;
	dwell.mode = mode;
}

/** A photograph in a run: `onplayedthrough` is where the run goes once it has rested. The run
    brought it up unless `reachedByRun` says a press did. */
function picture(onplayedthrough?: () => void, reachedByRun = true) {
	takeDown();
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(StillHarness, {
		target: host,
		props: { id: 'asset-1', mediaType: 'image', onplayedthrough, reachedByRun }
	});
	flushSync();
}

/** The bar's control for what happens at the end, whichever answer it shows. */
function endControl(): HTMLButtonElement {
	const glyph = host.querySelector(
		'.middle [aria-label^="Stop at"], .middle [aria-label^="Repeat"], .middle [aria-label^="Play through"]'
	);
	return glyph?.closest('button') as HTMLButtonElement;
}

beforeEach(() => {
	vi.useFakeTimers();
	vi.mocked(saveSettings).mockClear();
	run.shuffle = false;
});

afterEach(() => {
	takeDown();
	mini.close();
	vi.useRealTimers();
	run.shuffle = false;
	answers('loop_all');
});

describe('a picture moves a run on by the rule a clip end follows', () => {
	it('stays on the picture under Repeat this, however long it is looked at', () => {
		answers('loop_one');
		const next = vi.fn();
		picture(next);

		vi.advanceTimersByTime(PICTURE_SECONDS * 1000 * 30);

		expect(next, 'Repeat this moved the run off a picture').not.toHaveBeenCalled();
	});

	it('stays on the picture under Stop at the end, and walks on under Shuffle as a clip does', () => {
		answers('once');
		const next = vi.fn();
		picture(next);
		vi.advanceTimersByTime(PICTURE_SECONDS * 1000 * 30);
		expect(next, 'Stop at the end moved the run off a picture').not.toHaveBeenCalled();

		run.shuffle = true;
		flushSync();
		vi.advanceTimersByTime(PICTURE_SECONDS * 1000);
		expect(next).toHaveBeenCalledTimes(1);
	});

	it('rests and moves on under Play through', () => {
		answers('loop_all');
		const next = vi.fn();
		picture(next);

		vi.advanceTimersByTime(PICTURE_SECONDS * 1000 - 1);
		expect(next).not.toHaveBeenCalled();
		vi.advanceTimersByTime(1);
		expect(next).toHaveBeenCalledTimes(1);
	});

	it('waits on a picture a press opened until Play, which starts its rest', () => {
		answers('loop_all');
		const next = vi.fn();
		picture(next, false);

		vi.advanceTimersByTime(PICTURE_SECONDS * 1000 * 30);
		expect(next, 'a picture opened by a press moved the run on').not.toHaveBeenCalled();

		const play = host.querySelector('.player-bar button.play') as HTMLButtonElement;
		expect(play.disabled).toBe(false);
		expect(play.getAttribute('aria-label')).toBe('Play');
		play.click();
		flushSync();
		vi.advanceTimersByTime(PICTURE_SECONDS * 1000 - 1);
		expect(next).not.toHaveBeenCalled();
		vi.advanceTimersByTime(1);
		expect(next).toHaveBeenCalledTimes(1);
	});
});

describe('the control on a picture', () => {
	it('is pressable, reads the answer, and a press changes the answer the run follows', () => {
		answers('loop_all');
		const next = vi.fn();
		picture(next);
		const control = endControl();

		expect(control.disabled, 'the repeat control is dimmed on a picture').toBe(false);
		expect(control.getAttribute('aria-label')).toBe('Play through');
		expect(control.getAttribute('aria-pressed')).toBe('true');

		control.click();
		flushSync();

		expect(dwell.mode).toBe('loop_one');
		expect(saveSettings).toHaveBeenCalledWith({ 'playback.loop_mode': 'loop_one' });
		expect(host.querySelector('.middle [aria-label="Repeat this"]')).not.toBeNull();
		vi.advanceTimersByTime(PICTURE_SECONDS * 1000 * 30);
		expect(next, 'the press did not reach the run this picture is in').not.toHaveBeenCalled();
	});
});

describe('the bar on a picture in a run', () => {
	/** The bar's presses, left to right, by their glyph: the shape under the hand. The end's control
	    says its answer, which is the thing changing here, so it is read as the one control it is. */
	function barShape(): string[] {
		const ends = new Set(['Play through', 'Repeat this', 'Stop at the end']);
		return [...host.querySelectorAll('.player-bar button')].map((one) => {
			if (one.classList.contains('play')) return 'play';
			const name = one.getAttribute('aria-label') ?? '';
			return ends.has(name) ? 'repeat' : name;
		});
	}

	it('keeps one shape under each of the three answers, Play dimmed with the reason', () => {
		const shapes: string[][] = [];
		for (const mode of ['loop_all', 'loop_one', 'once'] as const) {
			answers(mode);
			picture(vi.fn());
			shapes.push(barShape());
			const play = host.querySelector('.player-bar button.play') as HTMLButtonElement | null;
			expect(play, `no Play on the bar under ${mode}`).not.toBeNull();
			expect(play?.disabled).toBe(mode !== 'loop_all');
			if (mode !== 'loop_all') {
				expect(play?.getAttribute('aria-label')).toBe(
					'A picture plays through only on Play through'
				);
			}
		}
		expect(shapes[0].length).toBeGreaterThan(2);
		expect(shapes[1]).toEqual(shapes[0]);
		expect(shapes[2]).toEqual(shapes[0]);
	});
});

describe('R and S on a picture', () => {
	function key(letter: string) {
		window.dispatchEvent(
			new KeyboardEvent('keydown', {
				key: letter,
				code: `Key${letter.toUpperCase()}`,
				bubbles: true
			})
		);
		flushSync();
	}

	it('answer as on a clip, each with its corner badge, and S shows its key on the tooltip', () => {
		answers('loop_all');
		picture(vi.fn());

		key('r');
		expect(dwell.mode).toBe('loop_one');
		expect(host.querySelector('.key-echoes [role="status"]')?.getAttribute('aria-label')).toBe(
			'Repeat this'
		);

		key('s');
		expect(run.shuffle).toBe(true);
		expect(
			[...host.querySelectorAll('.key-echoes [role="status"]')].map((one) =>
				one.getAttribute('aria-label')
			)
		).toContain('Shuffle');
		expect(keyOf('shuffle', 'picture')).toBe('player.shuffle');
	});
});

describe('the phone, on a picture', () => {
	/** The still's offer to the phone, mounted alone: nothing here needs the stage. */
	function offer() {
		takeDown();
		host = document.createElement('div');
		document.body.append(host);
		const view = mount(StillView, {
			target: host,
			props: { id: 'asset-1', mediaType: 'image', onplayedthrough: () => {} }
		}) as unknown as { remote: () => import('$lib/remote/offer.svelte').Offerable };
		mounted = view as unknown as Record<string, unknown>;
		flushSync();
		return view.remote();
	}

	it('offers the repeat press and Shuffle, and says which answer is on', () => {
		answers('loop_all');
		const remote = offer();

		expect(remote.state().repeat).toBe('loop_all');
		const repeat = remote.actions['player.repeat'];
		expect(repeat, 'the phone has no repeat press on a picture').toBeDefined();
		// Place 0 is Stop at the end, in the order the control cycles through.
		expect(repeat?.({ key: null, value: 0 })).toBe(true);
		expect(dwell.mode).toBe('once');

		expect(remote.actions['player.shuffle']?.({ key: null, value: 1 })).toBe(true);
		expect(run.shuffle).toBe(true);
	});
});
