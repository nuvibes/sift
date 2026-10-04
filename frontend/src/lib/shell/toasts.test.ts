import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
	DISMISS_AFTER_MS,
	ERROR_DISMISS_AFTER_MS,
	MAX_STACKED,
	oneSentence,
	toasts
} from './toasts.svelte';
import { place, thing } from '$lib/components/common/toast-pieces';

/* The rule worth testing here is the one about errors.
 *
 * A message that takes itself away after four seconds is a message someone can miss entirely (they
 * were looking at something else, or not at the screen at all), and a failure is the one worth not
 * missing. So an error is given longer, not forever: it outlasts the quiet ones, then goes on its
 * own, because a toast that never leaves is a scrap of the screen somebody has to clear by hand.
 */

beforeEach(() => {
	vi.useFakeTimers();
	toasts.clear();
});

afterEach(() => {
	toasts.clear();
	vi.useRealTimers();
});

describe('a message that is not an error', () => {
	it('takes itself away', () => {
		toasts.show('Tagged, nothing moved');
		expect(toasts.items).toHaveLength(1);

		vi.advanceTimersByTime(DISMISS_AFTER_MS);

		expect(toasts.items).toHaveLength(0);
	});

	it('stays until it has had its full time', () => {
		toasts.show('Saved', { tone: 'success' });

		vi.advanceTimersByTime(DISMISS_AFTER_MS - 1);

		expect(toasts.items).toHaveLength(1);
	});
});

describe('an error', () => {
	it('outlasts the quiet ones, then takes itself away', () => {
		toasts.show('That download failed', { tone: 'error' });

		// Past the timeout a message about something that went right would have left on, and still
		// here: the error is given longer on purpose.
		vi.advanceTimersByTime(DISMISS_AFTER_MS);
		expect(toasts.items).toHaveLength(1);
		expect(toasts.items[0].message).toBe('That download failed');

		// But not forever. Once its own, longer time is up it goes, like everything else.
		vi.advanceTimersByTime(ERROR_DISMISS_AFTER_MS - DISMISS_AFTER_MS);
		expect(toasts.items).toHaveLength(0);
	});

	it('can be taken away by hand before its time is up', () => {
		const id = toasts.show('That download failed', { tone: 'error' });

		vi.advanceTimersByTime(DISMISS_AFTER_MS);
		expect(toasts.items).toHaveLength(1);

		toasts.dismiss(id);
		expect(toasts.items).toHaveLength(0);
	});

	it('is not pushed out by things that went right', () => {
		// The hole in "an error never dismisses itself": it does not have to, if three cheerful
		// messages can shove it off the end of the stack. Tag a few things after a download failed and
		// the failure is gone, having been on screen for a second, announcing itself to nobody.
		toasts.show('That download failed', { tone: 'error' });
		toasts.show('Tagged');
		toasts.show('Tagged');
		toasts.show('Tagged');

		expect(toasts.items.some((t) => t.tone === 'error')).toBe(true);
	});

	it('gives way to a newer error once every slot is one', () => {
		// The other side of it: errors must not be able to wedge the stack shut. When there is nothing
		// cheerful left to drop, the oldest error goes and the newest is the one on screen.
		toasts.show('first failure', { tone: 'error' });
		toasts.show('second failure', { tone: 'error' });
		toasts.show('third failure', { tone: 'error' });
		toasts.show('fourth failure', { tone: 'error' });

		expect(toasts.items).toHaveLength(MAX_STACKED);
		expect(toasts.items.map((t) => t.message)).toEqual([
			'second failure',
			'third failure',
			'fourth failure'
		]);
	});

	it('does not keep the others from leaving', () => {
		// An error sitting there must not hold the whole strip open. The quiet ones still go.
		toasts.show('That download failed', { tone: 'error' });
		toasts.show('Tagged');

		vi.advanceTimersByTime(DISMISS_AFTER_MS);

		expect(toasts.items.map((t) => t.tone)).toEqual(['error']);
	});
});

describe('the stack', () => {
	it('never grows past three', () => {
		for (let i = 0; i < 6; i++) toasts.show(`message ${i}`);

		expect(toasts.items).toHaveLength(MAX_STACKED);
	});

	it('drops the oldest, so the newest is always the one you can see', () => {
		toasts.show('first');
		toasts.show('second');
		toasts.show('third');
		toasts.show('fourth');

		expect(toasts.items.map((t) => t.message)).toEqual(['second', 'third', 'fourth']);
	});

	it('forgets the timer of a message it dropped', () => {
		// A dropped toast's timer still fires unless it is cleared, and it fires with the id of a
		// toast that is gone. Harmless today. It is the kind of harmless that stops being harmless
		// when ids are reused.
		toasts.show('first');
		for (let i = 0; i < MAX_STACKED; i++) toasts.show(`later ${i}`);

		const before = toasts.items.length;
		vi.advanceTimersByTime(DISMISS_AFTER_MS * 2);

		expect(before).toBe(MAX_STACKED);
		expect(toasts.items).toHaveLength(0);
	});
});

describe('an action', () => {
	it('runs, and takes the message with it', () => {
		const undo = vi.fn();
		toasts.show('Moved to trash', { action: { label: 'Undo', run: undo } });

		const toast = toasts.items[0];
		toast.action?.run();
		toasts.dismiss(toast.id);

		expect(undo).toHaveBeenCalledOnce();
		expect(toasts.items).toHaveLength(0);
	});
});

/* --- work that is still running ---------------------------------------------------------- */

describe('a toast carrying progress', () => {
	/* The whole reason it exists. Everything else here announces something that has already
	 * happened, so four seconds is generous; work that is still running has to outlast it, and
	 * nobody knows in advance how long a file takes to cross a network. */
	it('does not leave on its own while the work is running', () => {
		toasts.show('Getting clip.mp4 ready...', { progress: { value: 0, max: 100 } });

		vi.advanceTimersByTime(ERROR_DISMISS_AFTER_MS * 3);

		expect(toasts.items).toHaveLength(1);
	});

	it('moves along, keeping its message unless a new one is given', () => {
		const id = toasts.show('Getting clip.mp4 ready...', { progress: { value: 0, max: 100 } });

		toasts.advance(id, { value: 40, max: 100 });

		expect(toasts.items[0]?.progress).toEqual({ value: 40, max: 100 });
		expect(toasts.items[0]?.message).toBe('Getting clip.mp4 ready...');
	});

	it('starts an ordinary timer once the work is over', () => {
		const id = toasts.show('Getting clip.mp4 ready...', { progress: { value: 0, max: 100 } });

		toasts.settle(id, 'clip.mp4 is ready. Drag it again.', 'success');

		expect(toasts.items[0]?.progress).toBeUndefined();
		expect(toasts.items[0]?.message).toBe('clip.mp4 is ready. Drag it again.');
		vi.advanceTimersByTime(DISMISS_AFTER_MS + 1);
		expect(toasts.items).toHaveLength(0);
	});

	/* Somebody dismissed the message while the file was still coming. Finishing must not bring it
	 * back. A toast that reappears after being closed is the one thing a dismiss must never do. */
	it('does not come back after it has been dismissed', () => {
		const id = toasts.show('Getting clip.mp4 ready...', { progress: { value: 0, max: 100 } });
		toasts.dismiss(id);

		toasts.advance(id, { value: 90, max: 100 });
		toasts.settle(id, 'Ready.', 'success');

		expect(toasts.items).toHaveLength(0);
	});

	/* An unmeasured fetch: the server never said how big the file is. Null draws a sweep rather
	 * than a bar that has not moved, which are two different things to look at. */
	it('carries an unknown position as null rather than as zero', () => {
		const id = toasts.show('Getting clip.mp4 ready...', { progress: { value: null, max: 0 } });
		toasts.advance(id, { value: null, max: 0 });

		expect(toasts.items[0]?.progress?.value).toBeNull();
	});
});

describe('a message somebody is reading', () => {
	/* Four seconds is enough to read a short sentence and is not enough to read one, decide it was
	 * not what you wanted, and reach the Undo before it goes. Undo is only ever offered here, so a
	 * message that leaves under the hand takes the action back with it.
	 *
	 * A PAUSE rather than a reset, and the difference is the whole of the bookkeeping: what is left
	 * is banked and handed back, so a toast held three seconds into its four has one second when it
	 * is let go, not four.
	 */

	it('does not leave while it is being held', () => {
		const id = toasts.show('Tagged, nothing moved');
		vi.advanceTimersByTime(1000);
		toasts.hold(id);

		vi.advanceTimersByTime(DISMISS_AFTER_MS * 5);

		expect(toasts.items).toHaveLength(1);
	});

	it('is given back exactly what was left, and not the whole time again', () => {
		const id = toasts.show('Tagged, nothing moved');
		vi.advanceTimersByTime(DISMISS_AFTER_MS - 1000);
		toasts.hold(id);
		vi.advanceTimersByTime(60_000);
		toasts.release(id);

		// A second short of its four, so a moment before that it is still there...
		vi.advanceTimersByTime(900);
		expect(toasts.items).toHaveLength(1);
		// ...and a moment after it, it is not. Starting the clock over would leave it up for four.
		vi.advanceTimersByTime(200);
		expect(toasts.items).toHaveLength(0);
	});

	it('a clock that was never started is not banked, so holding a running job is nothing', () => {
		// A toast with work still in flight has no timer at all. If holding one banked a remainder,
		// letting go would start a clock the toast was never on, and the message about work is the
		// one that has to outlast the work.
		const id = toasts.show('Downloading...', { progress: { value: null, max: 1 } });
		toasts.hold(id);
		toasts.release(id);

		vi.advanceTimersByTime(ERROR_DISMISS_AFTER_MS * 5);
		expect(toasts.items).toHaveLength(1);
	});

	it('needs every hold released, not just one of them', () => {
		// The pointer can be on a toast while the keyboard is inside it, and either one leaving
		// would otherwise start the clock while the other was still there.
		const id = toasts.show('Tagged, nothing moved');
		toasts.hold(id);
		toasts.hold(id);
		toasts.release(id);

		vi.advanceTimersByTime(DISMISS_AFTER_MS * 3);
		expect(toasts.items).toHaveLength(1);

		toasts.release(id);
		vi.advanceTimersByTime(DISMISS_AFTER_MS + 1);
		expect(toasts.items).toHaveLength(0);
	});

	it('holds a job that settles under the pointer, rather than starting its clock', () => {
		// A running toast has no timer at all. Settling arms one, and arming it while somebody is
		// reading it would make the message about work most worth reading the fastest to leave.
		const id = toasts.show('Downloading...', { progress: { value: null, max: 1 } });
		toasts.hold(id);
		toasts.settle(id, 'Downloaded.', 'success');

		vi.advanceTimersByTime(DISMISS_AFTER_MS * 3);
		expect(toasts.items).toHaveLength(1);

		toasts.release(id);
		vi.advanceTimersByTime(DISMISS_AFTER_MS + 1);
		expect(toasts.items).toHaveLength(0);
	});

	it('an error held and let go still gets the longer time', () => {
		const id = toasts.show('That did not work', { tone: 'error' });
		toasts.hold(id);
		vi.advanceTimersByTime(60_000);
		toasts.release(id);

		vi.advanceTimersByTime(DISMISS_AFTER_MS + 1);
		expect(toasts.items).toHaveLength(1);
		vi.advanceTimersByTime(ERROR_DISMISS_AFTER_MS);
		expect(toasts.items).toHaveLength(0);
	});

	it('releasing something that was never held does nothing at all', () => {
		const id = toasts.show('Tagged, nothing moved');
		toasts.release(id);

		vi.advanceTimersByTime(DISMISS_AFTER_MS - 100);
		expect(toasts.items).toHaveLength(1);
		vi.advanceTimersByTime(200);
		expect(toasts.items).toHaveLength(0);
	});
});

/*
 * A toast that names a thing is built of pieces, the shape a History line is drawn from: the thing
 * carries its kind and id, and the words joined are the message a screen reader is handed.
 */
describe('a message that names something', () => {
	it('keeps the thing as a piece with its kind and id, beside the words', () => {
		toasts.show(['The file was added to the Photo Set ', thing('photo_set', 'ps1', 'beach days')]);

		expect(toasts.items[0].pieces).toEqual([
			{
				text: 'The file was added to the Photo Set ',
				kind: null,
				id: null,
				href: null,
				gone: false,
				rest: [],
				lead: ''
			},
			{
				text: 'beach days',
				kind: 'photo_set',
				id: 'ps1',
				href: null,
				gone: false,
				rest: [],
				lead: ''
			}
		]);
		expect(toasts.items[0].message).toBe('The file was added to the Photo Set beach days');
	});

	it('takes the stop off one sentence ending in words, and never cuts a name', () => {
		toasts.show(['Sift is reading ', thing('folder', 'r1', 'Clips'), ' where it is now.']);
		toasts.show(['Renamed to ', thing('site', 's1', 'acme inc.')]);

		expect(toasts.items[0].message).toBe('Sift is reading Clips where it is now');
		expect(toasts.items[0].pieces.at(-1)?.text).toBe(' where it is now');
		expect(toasts.items[1].message).toBe('Renamed to acme inc.');
	});

	it('keeps every stop where the words are several sentences', () => {
		toasts.show([
			'Sift has forgotten ',
			thing('folder', 'r1', 'Clips'),
			'. The files are untouched.'
		]);

		expect(toasts.items[0].message).toBe('Sift has forgotten Clips. The files are untouched.');
	});

	it('carries a place by its address', () => {
		toasts.show(['Follow it in ', place('Activity', '/settings/jobs')]);

		expect(toasts.items[0].pieces[1]).toMatchObject({ text: 'Activity', href: '/settings/jobs' });
	});

	it('draws a plain message as one run of words', () => {
		toasts.show('Link copied');

		expect(toasts.items[0].pieces).toHaveLength(1);
		expect(toasts.items[0].pieces[0]).toMatchObject({ text: 'Link copied', kind: null });
	});
});

/*
 * THE FULL STOP, AND WHY ONE SENTENCE DOES NOT GET ONE.
 *
 * A single line of feedback that ends in a stop ("Link copied.") reads as a paragraph somebody has
 * to finish, where the same words without one read as a label. Two or more sentences keep every one
 * of their stops, because then the stops are doing the work they exist for.
 *
 * It lives in `show` rather than at the call sites because some of the sentences are not written at
 * a call site at all: a refusal the server explains arrives as `detail` and goes through this same
 * door. A rule applied where the words are typed would be a rule those walked past.
 */
describe('the full stop on a toast', () => {
	it('takes a lone stop off a message that is one sentence', () => {
		toasts.show('Link copied.');

		expect(toasts.items[0].message).toBe('Link copied');
	});

	it('leaves every stop on a message that is two', () => {
		toasts.show('Downloaded. Drag it in from your downloads.');

		expect(toasts.items[0].message).toBe('Downloaded. Drag it in from your downloads.');
	});

	it('leaves a message that is still running whole', () => {
		// Three dots are not a full stop: they say the work is going on, which is the opposite of a
		// finished sentence, and taking one of them off would leave "Downloading.."
		toasts.show('Downloading...', { icon: 'download' });

		expect(toasts.items[0].message).toBe('Downloading...');
	});

	it('leaves a question and an exclamation alone', () => {
		toasts.show('Is that the right file?');
		toasts.show('Careful!');

		expect(toasts.items.map((one) => one.message)).toEqual(['Is that the right file?', 'Careful!']);
	});

	it('applies the same rule to the words a finished job settles with', () => {
		const id = toasts.show('Working...', { progress: { value: null, max: 1 } });
		toasts.settle(id, 'That is done.', 'success');

		expect(toasts.items[0].message).toBe('That is done');
	});

	it('is a rule about sentences, not about the words the caller happened to write', () => {
		// The server's own refusal comes through this door as `detail` (see `bulk.announceRefusal`),
		// so the rule has to hold for a sentence nothing in this repository typed.
		toasts.show('Sift is not allowed to change files in Videos.', { tone: 'error' });

		expect(toasts.items[0].message).toBe('Sift is not allowed to change files in Videos');
	});

	it('says whether a message holds one sentence or several', () => {
		expect(oneSentence('Link copied.')).toBe(true);
		expect(oneSentence('Tagged 4 files')).toBe(true);
		expect(oneSentence('Downloaded. Drag it in from your downloads.')).toBe(false);
		expect(oneSentence('Is that right? It looked wrong.')).toBe(false);
	});
});
