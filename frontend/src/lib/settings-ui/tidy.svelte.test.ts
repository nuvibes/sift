import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Tidy } from './tidy.svelte';

/* The tidy-up half of Maintenance.
 *
 * Three decisions live here rather than in markup: that a request which failed does not read as
 * "nothing to do", that every kind is listed with the ones needing work first, and that the counts
 * after a run come from the server rather than being adjusted locally.
 */

const get = vi.fn();
const post = vi.fn();

vi.mock('$lib/api/client', () => ({
	api: {
		get: (path: string) => get(path),
		post: (path: string) => post(path)
	},
	ApiError: class extends Error {
		detail?: string;
	}
}));

function leftovers(
	count: number | null,
	name = 'stranded-assets',
	surveyed_at: number | null = null
) {
	return {
		name,
		title: 'Files missing from every folder',
		detail: 'why',
		noun: 'file',
		nouns: 'files',
		count,
		frees_bytes: null,
		surveyed_at
	};
}

beforeEach(() => {
	get.mockReset();
	post.mockReset();
});

describe('what has built up', () => {
	it('lists every kind, with the ones needing work first', async () => {
		/* Listing only what had built up would make a kind with nothing to remove indistinguishable
		   from one that does not exist: somebody looking for the control that clears a particular
		   thing would find an empty space and could not tell whether they were looking in the wrong
		   place. */
		get.mockResolvedValue({
			leftovers: [leftovers(0, 'settled-failures'), leftovers(3)],
			surveying: false
		});
		const view = new Tidy();

		await view.load();

		expect(view.worthDoing.map((one) => one.name)).toEqual(['stranded-assets', 'settled-failures']);
	});

	it('does not read a count that was never taken as something to do, or as nothing', async () => {
		/* Two of the counts read the disk and arrive as null until a survey has been taken. Null
		   is neither "there is something here" nor "there is nothing", and it says which counts
		   are still waiting, and when the rest were taken. */
		get.mockResolvedValue({
			leftovers: [leftovers(null, 'leftover-derivatives'), leftovers(0), leftovers(2, 'x', 500)],
			surveying: false
		});
		const view = new Tidy();

		await view.load();

		expect(view.worthDoing.map((one) => one.name)).toEqual([
			'x',
			'leftover-derivatives',
			'stranded-assets'
		]);
		expect(view.unsurveyed).toBe(true);
		expect(view.lastSurveyed).toBe(500);
	});

	it('asks for a survey and reads as counting until the queue says otherwise', async () => {
		get.mockResolvedValue({
			leftovers: [leftovers(null, 'leftover-derivatives')],
			surveying: false
		});
		post.mockResolvedValue({ queued: true });
		const view = new Tidy();
		await view.load();
		expect(view.surveying).toBe(false);
		expect(view.lastSurveyed).toBeNull();

		await view.survey();

		expect(post).toHaveBeenCalledWith('/tidy/survey');
		expect(view.surveying).toBe(true);

		// The queue moved and the screen re-read: the survey is in, and the count with it.
		get.mockResolvedValue({
			leftovers: [leftovers(4, 'leftover-derivatives', 900)],
			surveying: false
		});
		await view.load();
		expect(view.surveying).toBe(false);
		expect(view.lastSurveyed).toBe(900);
		expect(view.unsurveyed).toBe(false);
	});

	it('says what went wrong when a survey is refused, and is not counting', async () => {
		get.mockResolvedValue({ leftovers: [], surveying: false });
		post.mockRejectedValue(new Error('refused'));
		const view = new Tidy();
		await view.load();

		await view.survey();

		expect(view.problem).toBeTruthy();
		expect(view.surveying).toBe(false);
	});

	it('knows the difference between listing everything and having something to do', async () => {
		/* The screen keys its "nothing has built up" sentence off this. Read off the length of the
		   list instead, it would never say it, since the list is always full. */
		get.mockResolvedValue({
			leftovers: [leftovers(0), leftovers(0, 'settled-failures')]
		});
		const view = new Tidy();

		await view.load();

		expect(view.worthDoing).toHaveLength(2);
		expect(view.anythingToDo).toBe(false);
	});

	it('does not read as "nothing to do" when the request failed', async () => {
		get.mockRejectedValue(new Error('down'));
		const view = new Tidy();

		await view.load();

		// The screen keys its wording off this. Set in the failure path too, it would say the
		// library is clean on the strength of a request nobody answered.
		expect(view.loaded).toBe(false);
		expect(view.problem).toBeTruthy();
	});

	it('takes the counts after a run from the server, not from arithmetic', async () => {
		const one = leftovers(3);
		get.mockResolvedValue({ leftovers: [one] });
		// Removing rows strands the files they named, so a second entry GROWS while the first
		// empties. Nothing here could work that out, which is why the server sends it.
		post.mockResolvedValue({
			removed: 3,
			leftovers: [leftovers(0), { ...leftovers(9, 'leftover-derivatives') }]
		});
		const view = new Tidy();
		await view.load();

		await view.run(one);

		expect(post).toHaveBeenCalledWith('/tidy/stranded-assets');
		// The emptied one is still listed, and now last.
		expect(view.worthDoing.map((entry) => entry.count)).toEqual([9, 0]);
		expect(view.lastRun).toEqual({
			title: one.title,
			removed: 3,
			noun: one.noun,
			nouns: one.nouns
		});
	});

	it('says what went wrong and changes nothing when a run is refused', async () => {
		const one = leftovers(3);
		get.mockResolvedValue({ leftovers: [one] });
		post.mockRejectedValue(new Error('refused'));
		const view = new Tidy();
		await view.load();

		await view.run(one);

		expect(view.problem).toBeTruthy();
		expect(view.worthDoing).toHaveLength(1);
		expect(view.lastRun).toBeNull();
	});
});

/* Bringing the hover clips up to the shape that is set.
 *
 * Its own count and its own run, not a corner of the rebuild beside it. They answer different
 * questions, and one button doing both would make changing a preference cost a full re-thumbnail
 * of the library.
 */
describe('the hover clips', () => {
	it('counts the files whose clip is a different shape', async () => {
		get.mockImplementation((path: string) =>
			path === '/jobs/rebuild-previews'
				? Promise.resolve({ total: 47 })
				: Promise.resolve({ leftovers: [], total: 0 })
		);
		const tidy = new Tidy();

		await tidy.load();

		expect(tidy.restyleable).toBe(47);
	});

	it('leaves the count unknown when it cannot be read', async () => {
		get.mockImplementation((path: string) =>
			path === '/jobs/rebuild-previews'
				? Promise.reject(new Error('no'))
				: Promise.resolve({ leftovers: [], total: 0 })
		);
		const tidy = new Tidy();

		await tidy.load();

		// Unknown rather than zero. Zero would disable the button while claiming the library is up
		// to date, which is a statement nobody made.
		expect(tidy.restyleable).toBeNull();
	});

	it('says so once a rebuild has been accepted', async () => {
		post.mockResolvedValue({ total: 3, queued: 1 });
		const tidy = new Tidy();

		expect(await tidy.rebuildPreviews()).toBeUndefined();

		expect(post).toHaveBeenCalledWith('/jobs/rebuild-previews');
		expect(tidy.restyling).toBe(true);
	});

	it('reports a refusal rather than claiming it started', async () => {
		post.mockRejectedValue(new Error('nope'));
		const tidy = new Tidy();

		expect(await tidy.rebuildPreviews()).toBeTruthy();
		expect(tidy.restyling).toBe(false);
	});

	it('is not busy once a refusal has been reported', async () => {
		post.mockRejectedValue(new Error('nope'));
		const tidy = new Tidy();

		await tidy.rebuildPreviews();

		// A screen left busy after a failure is one where every other button stays disabled for
		// the rest of the visit, with nothing saying why.
		expect(tidy.busy).toBe(false);
	});
});
