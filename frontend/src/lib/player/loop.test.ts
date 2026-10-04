/* The A-B loop, and which clip it belongs to.
 *
 * One object, read by every player on screen. That is what lets a clip keep its marks when it is
 * handed between the full-size view and the panel in the corner, and it is what could make a loop
 * set on one file also jump the other one back, which is the thing these tests are here for.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import { abLoop, Loop } from './loop.svelte';

beforeEach(() => abLoop.reset());

describe('who the marks belong to', () => {
	it('is the clip they were set on, and only that clip', () => {
		abLoop.mark('a-clip', 10);
		abLoop.mark('a-clip', 20);

		expect(abLoop.owns('a-clip')).toBe(true);
		expect(abLoop.owns('something-else'), 'another clip was told the loop was its own').toBe(false);
		expect(abLoop.running).toBe(true);
	});

	it('moves to whatever was marked last', () => {
		// One loop, on the thing somebody last pointed at. Marking a second clip does not leave two.
		abLoop.mark('a-clip', 10);
		abLoop.mark('a-clip', 20);
		abLoop.mark('b-clip', 5);

		expect(abLoop.owns('b-clip')).toBe(true);
		expect(abLoop.owns('a-clip')).toBe(false);
		expect(abLoop.a).toBe(5);
		expect(abLoop.b, 'the second clip inherited an end from the first').toBeNull();
	});

	it('will not let the loop run backwards', () => {
		// A second press before the first point is somebody correcting themselves.
		abLoop.mark('a-clip', 30);
		abLoop.mark('a-clip', 5);

		expect(abLoop.a).toBe(5);
		expect(abLoop.b).toBeNull();
	});

	it('clears on a third press', () => {
		abLoop.mark('a-clip', 10);
		abLoop.mark('a-clip', 20);
		abLoop.mark('a-clip', 25);

		expect(abLoop.a).toBeNull();
		expect(abLoop.b).toBeNull();
	});
});

describe('how long the marks last', () => {
	/* A turn, because the check that everybody has gone is deferred by one. See `unwatch`. */
	const aTurn = () => new Promise((resolve) => setTimeout(resolve, 1));

	it('forgets them once the last player showing the clip has gone', async () => {
		abLoop.watch('a-clip');
		abLoop.mark('a-clip', 10);
		abLoop.mark('a-clip', 20);

		abLoop.unwatch('a-clip');
		await aTurn();

		expect(abLoop.running).toBe(false);
		expect(abLoop.owns('a-clip')).toBe(false);
	});

	it('keeps them while another player is still showing the same clip', async () => {
		/* The full-size view and the panel in the corner, both on one clip. The one going away must
		 * not take the marks the other is still drawing. */
		abLoop.watch('a-clip');
		abLoop.watch('a-clip');
		abLoop.mark('a-clip', 10);
		abLoop.mark('a-clip', 20);

		abLoop.unwatch('a-clip');
		await aTurn();

		expect(abLoop.running, 'a departing player cleared a loop another was showing').toBe(true);
		expect(abLoop.owns('a-clip')).toBe(true);
	});

	it('survives a handover where the old player leaves before the new one arrives', async () => {
		/* Swapping the full-size view for the corner panel destroys one
		 * player and builds another, and the order is not guaranteed, so the count touches zero for
		 * an instant. Clearing on the spot would end a loop on a clip that never left the screen. */
		abLoop.watch('a-clip');
		abLoop.mark('a-clip', 10);
		abLoop.mark('a-clip', 20);

		abLoop.unwatch('a-clip');
		abLoop.watch('a-clip');
		await aTurn();

		expect(abLoop.running, 'the loop did not survive a handover between players').toBe(true);
		expect(abLoop.owns('a-clip')).toBe(true);
	});

	it('leaves them alone when a player of something else goes away', async () => {
		abLoop.watch('a-clip');
		abLoop.watch('b-clip');
		abLoop.mark('a-clip', 10);
		abLoop.mark('a-clip', 20);

		abLoop.unwatch('b-clip');
		await aTurn();

		expect(abLoop.running, 'a second player cleared a loop that was not its own').toBe(true);
		expect(abLoop.owns('a-clip')).toBe(true);
	});
});

describe('dragging a mark by hand', () => {
	it('will not push the start past the end', () => {
		abLoop.mark('a-clip', 10);
		abLoop.mark('a-clip', 20);

		abLoop.moveTo('a', 45);

		expect(abLoop.a, 'the start was dragged past the end').toBe(20);
		expect(abLoop.b).toBe(20);
	});

	it('will not pull the end back before the start', () => {
		abLoop.mark('a-clip', 10);
		abLoop.mark('a-clip', 20);

		abLoop.moveTo('b', 4);

		expect(abLoop.b, 'the end was dragged before the start').toBe(10);
		expect(abLoop.a).toBe(10);
	});

	it('moves a mark that has nothing to cross yet', () => {
		// Only the start is set, so there is no end for it to run into and it goes where it is put.
		abLoop.mark('a-clip', 10);

		abLoop.moveTo('a', 90);

		expect(abLoop.a).toBe(90);
		expect(abLoop.b).toBeNull();
	});

	it('moves each mark to where it was dragged when neither crosses the other', () => {
		abLoop.mark('a-clip', 10);
		abLoop.mark('a-clip', 20);

		abLoop.moveTo('a', 12);
		abLoop.moveTo('b', 18);

		expect(abLoop.a).toBe(12);
		expect(abLoop.b).toBe(18);
	});
});

describe('what the one button says it will do next', () => {
	it('offers each step in turn, and starting over after both', () => {
		expect(abLoop.nextAction).toBe('Set the loop start');

		abLoop.mark('a-clip', 10);
		expect(abLoop.nextAction).toBe('Set the loop end');

		abLoop.mark('a-clip', 20);
		expect(abLoop.nextAction).toBe('Clear the loop');
	});
});

describe('a loop of its own', () => {
	/* The reason the class is exported and not only the one instance.
	 *
	 * The shared object tells CLIPS apart, which is enough while the only two things reading it are
	 * two ways of showing the same clip. It is not enough once several frames are on screen at once
	 * and two of them are allowed to hold the same file: `owns` says yes to both, so marks made in
	 * one frame are enforced in the other: a video jumping backwards with nothing to explain it.
	 */
	it('keeps marks made in one frame out of another showing the same file', () => {
		const first = new Loop();
		const second = new Loop();

		first.mark('a-clip', 10);
		first.mark('a-clip', 20);

		expect(first.running).toBe(true);
		expect(second.running, 'a loop marked in one frame reached another').toBe(false);
		expect(second.owns('a-clip'), 'a second frame was told the marks were its own').toBe(false);
	});

	it('does not disturb the loop the players share', () => {
		abLoop.mark('a-clip', 10);
		abLoop.mark('a-clip', 20);

		new Loop().mark('a-clip', 55);

		expect(abLoop.a).toBe(10);
		expect(abLoop.b).toBe(20);
	});

	it('behaves the same way the shared one does', () => {
		const own = new Loop();

		own.mark('a-clip', 10);
		expect(own.nextAction).toBe('Set the loop end');
		own.mark('a-clip', 20);
		expect(own.running).toBe(true);
		own.mark('a-clip', 25);
		expect(own.a).toBeNull();
		expect(own.b).toBeNull();
	});

	it('is armed whole from a stretch somebody saved, and claims the clip', () => {
		const own = new Loop();

		own.arm('a-clip', 12, 27);

		expect(own.owns('a-clip')).toBe(true);
		expect(own.a).toBe(12);
		expect(own.b).toBe(27);
		expect(own.running, 'an armed pair has to actually repeat').toBe(true);
	});

	it('refuses a stretch whose end is not after its start', () => {
		const own = new Loop();
		own.arm('a-clip', 12, 27);

		own.arm('a-clip', 30, 30);
		own.arm('a-clip', 40, 5);

		expect(own.a, 'a pair that cannot contain its own playhead was accepted').toBe(12);
		expect(own.b).toBe(27);
	});

	it('replaces what was marked by hand, because opening one is what somebody just asked for', () => {
		const own = new Loop();
		own.mark('another-clip', 3);

		own.arm('a-clip', 12, 27);

		expect(own.owns('another-clip')).toBe(false);
		expect(own.owns('a-clip')).toBe(true);
	});

	it('is still forgotten when the last player showing the clip goes', () => {
		// Arming is not persisting. The note at the top of the module is the whole reason a mark is
		// written down nowhere, and a saved loop opened deliberately must not change that: leave the
		// clip and the marks go with it.
		const own = new Loop();
		own.watch('a-clip');
		own.arm('a-clip', 12, 27);

		own.unwatch('a-clip');
		return new Promise<void>((done) =>
			setTimeout(() => {
				expect(own.a, 'the armed pair outlived the clip being on screen').toBeNull();
				expect(own.owner).toBeNull();
				done();
			}, 0)
		);
	});
});
