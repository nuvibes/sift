import { describe, expect, it } from 'vitest';
import type { JobsPage } from './family';
import { kindChoices } from './kinds';
import { OLDER_KINDS } from './queue.svelte';

/* The list's Type choice: the kinds the server names, by their names, and ONE "Older tasks" for
 * every kind this version no longer runs, whose stored id is never a word on the list. */

function page(over: Partial<JobsPage>): JobsPage {
	return {
		jobs: [],
		total: 0,
		counts: {},
		tallies: {},
		by_type: {},
		names: {},
		older: [],
		work: {},
		families: {},
		housekeeping: [],
		stepping_back: false,
		step_back_share: 25,
		full_amount: false,
		password_wanted: 0,
		...over
	};
}

const HELD = page({
	names: {
		fingerprint_stash_box: 'Fingerprinting for duplicates',
		thumbnail: 'Generating thumbnail'
	},
	older: ['face_asked_only', 'download_undouble_names'],
	by_type: {
		thumbnail: { done: 4 },
		face_asked_only: { done: 2, failed: 1 },
		download_undouble_names: { done: 3 }
	}
});

describe('the Type choice', () => {
	it('offers every named kind by its name, then one choice for the older kinds', () => {
		expect(kindChoices(HELD, null, 'Older tasks')).toEqual([
			{ value: 'fingerprint_stash_box', label: 'Fingerprinting for duplicates' },
			{ value: 'thumbnail', label: 'Generating thumbnail' },
			{ value: OLDER_KINDS, label: 'Older tasks' }
		]);
	});

	it('never offers a stored id as a word', () => {
		const words = kindChoices(HELD, 'face_asked_only', 'Older tasks').map((one) => one.label);
		expect(words).not.toContain('face_asked_only');
		expect(words).not.toContain('download_undouble_names');
	});

	it('offers no older choice while the table holds none, unless it is the one chosen', () => {
		const none = page({ names: { thumbnail: 'Generating thumbnail' } });
		expect(kindChoices(none, null, 'Older tasks').map((one) => one.value)).toEqual(['thumbnail']);
		expect(kindChoices(none, OLDER_KINDS, 'Older tasks').at(-1)?.value).toBe(OLDER_KINDS);
	});

	it('keeps a chosen kind whose last row went, under the name it was given', () => {
		const gone = page({ names: {} });
		expect(
			kindChoices(gone, 'thumbnail', 'Older tasks', { thumbnail: 'Generating thumbnail' })
		).toEqual([{ value: 'thumbnail', label: 'Generating thumbnail' }]);
	});
});
