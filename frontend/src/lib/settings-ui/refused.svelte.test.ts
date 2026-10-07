import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setCsrfToken } from '$lib/api/client';
import { Refused, aboutQuarantined, explain, type Quarantined } from './refused.svelte';

/* What Sift would not take, as the Maintenance screen sees it.
 *
 * Two things are worth testing here and neither is the fetching. The first is that the two piles
 * stay apart: a file Sift MOVED is in a folder of its own and can be deleted from this screen, and
 * a file Sift LEFT ALONE is untouched in somebody's library and cannot, so a store that merged
 * them would make one button mean two things.
 *
 * The second is what the screen says about a file it knows nothing about. A refusal recorded before
 * notes existed has neither a reason nor an origin, and composing the two unknowns would produce
 * a sentence that says the same thing twice and reads like a fault.
 */

const fetchMock = vi.fn();

const MOVED: Quarantined = {
	id: 'abc-payload.mp4',
	original_name: 'invoice.pdf',
	reason: 'signature_not_allowed',
	detected: 'pdf',
	origin: 'download',
	size_bytes: 34,
	quarantined_at: 1_700_000_000,
	explained: true
};

const QUARANTINE = {
	moved: [MOVED],
	left_alone: [
		{
			root_id: '01HX0000000000000000000003',
			root_name: 'media',
			rejections_total: 1,
			rejections: [
				{
					rel_path: 'clips/broken.mp4',
					reason: 'not_decodable',
					detected: 'text',
					size_bytes: 12,
					first_seen_at: 0,
					last_seen_at: 0
				}
			]
		}
	],
	keep_days: 30
};

const SAVES = { items: [{ id: 'a-save', user_id: 'u', asset_id: 'a', saved_at: 1 }], total: 1 };

beforeEach(() => {
	vi.stubGlobal('fetch', fetchMock);
	fetchMock.mockReset();
	setCsrfToken('a-token');
});

afterEach(() => {
	vi.unstubAllGlobals();
	setCsrfToken(null);
});

function answers(...responses: Array<{ ok: boolean; status?: number; body?: unknown }>) {
	for (const response of responses) {
		fetchMock.mockResolvedValueOnce({
			ok: response.ok,
			status: response.status ?? (response.ok ? 200 : 409),
			json: async () => response.body ?? {}
		});
	}
}

describe('reading what was refused', () => {
	it('keeps the two piles apart and counts the one nobody can act on', async () => {
		answers({ ok: true, body: QUARANTINE }, { ok: true, body: SAVES });
		const view = new Refused();

		await view.load();

		expect(view.moved.map((one) => one.id)).toEqual(['abc-payload.mp4']);
		expect(view.leftAlone[0].root_name).toBe('media');
		expect(view.skippedCount).toBe(1);
		expect(view.keepDays).toBe(30);
		expect(view.savesTotal).toBe(1);
		expect(view.loaded).toBe(true);
		expect(view.problem).toBeNull();
	});

	it('counts skipped files across every folder rather than the first', async () => {
		answers(
			{
				ok: true,
				body: {
					...QUARANTINE,
					left_alone: [
						QUARANTINE.left_alone[0],
						{
							root_id: 'two',
							root_name: 'other',
							rejections_total: 2,
							rejections: [
								{ ...QUARANTINE.left_alone[0].rejections[0], rel_path: 'a.mp4' },
								{ ...QUARANTINE.left_alone[0].rejections[0], rel_path: 'b.mp4' }
							]
						}
					]
				}
			},
			{ ok: true, body: SAVES }
		);
		const view = new Refused();

		await view.load();

		expect(view.skippedCount).toBe(3);
	});

	it('says what went wrong and stays unloaded, rather than drawing an empty screen', async () => {
		/* A failed read that left `loaded` true would draw "nothing was refused", which is the
		   reassuring reading of silence, and the wrong one for a screen about files Sift rejected. */
		/* Both reads are asked for together, so both are answered: queueing one leaves the other
		   with no answer at all, which is a different failure from the one under test. */
		answers(
			{ ok: false, status: 500, body: { detail: 'the database is locked' } },
			{ ok: false, status: 500, body: { detail: 'the database is locked' } }
		);
		const view = new Refused();

		await view.load();

		expect(view.loaded).toBe(false);
		expect(view.problem).toBe('the database is locked');
		expect(view.moved).toEqual([]);
	});

	it('says Sift could not be reached when there is no answer at all', async () => {
		fetchMock.mockRejectedValueOnce(new TypeError('failed to fetch'));
		const view = new Refused();

		await view.load();

		expect(view.problem).toContain("Sift couldn't be reached");
	});

	it('is not loading once the attempt has finished, however it finished', async () => {
		answers(
			{ ok: false, status: 500, body: { detail: 'no' } },
			{ ok: false, status: 500, body: { detail: 'no' } }
		);
		const view = new Refused();

		await view.load();

		expect(view.loading).toBe(false);
	});
});

describe('acting on one', () => {
	it('deletes a quarantined file by its name and reads the screen again', async () => {
		answers(
			{ ok: true, body: {} },
			{ ok: true, body: { ...QUARANTINE, moved: [] } },
			{ ok: true, body: SAVES }
		);
		const view = new Refused();

		expect(await view.remove(MOVED)).toBeUndefined();

		const [address, options] = fetchMock.mock.calls[0];
		expect(String(address)).toContain('/library/quarantine/abc-payload.mp4');
		expect(options.method).toBe('DELETE');
		expect(view.moved).toEqual([]);
		expect(view.busy).toBe(false);
	});

	it('escapes a name before putting it in the address', async () => {
		/* The name is whatever is on disk and it goes into a URL. A space or a `#` left raw would
		   address a different file, or none. */
		answers({ ok: true, body: {} }, { ok: true, body: QUARANTINE }, { ok: true, body: SAVES });
		const view = new Refused();

		await view.remove({ ...MOVED, id: 'a file #2.mp4' });

		expect(String(fetchMock.mock.calls[0][0])).toContain('a%20file%20%232.mp4');
	});

	it('hands back what went wrong rather than throwing, and stops being busy', async () => {
		answers({
			ok: false,
			status: 404,
			body: { detail: 'there is no quarantined file of that name' }
		});
		const view = new Refused();

		expect(await view.remove(MOVED)).toBe('there is no quarantined file of that name');
		expect(view.busy).toBe(false);
	});

	it('forgets a refusal by naming the folder and the path inside it', async () => {
		answers({ ok: true, body: {} }, { ok: true, body: QUARANTINE }, { ok: true, body: SAVES });
		const view = new Refused();

		expect(await view.allow('01HX0000000000000000000003', 'clips/broken.mp4')).toBeUndefined();

		const [address, options] = fetchMock.mock.calls[0];
		expect(String(address)).toContain('/library/roots/01HX0000000000000000000003/rejections/allow');
		expect(JSON.parse(options.body)).toEqual({ rel_path: 'clips/broken.mp4' });
	});

	it('hands back what went wrong when forgetting one is refused', async () => {
		answers({
			ok: false,
			status: 404,
			body: { detail: 'there is no library folder with that id' }
		});
		const view = new Refused();

		expect(await view.allow('nope', 'clips/broken.mp4')).toBe(
			'there is no library folder with that id'
		);
		expect(view.busy).toBe(false);
	});
});

describe('saying why in words rather than in the name of a rule', () => {
	it('says what the bytes looked like, in words rather than a media type', () => {
		expect(explain('signature_not_allowed', 'application/pdf')).toBe(
			"Its bytes aren't a picture or a video; it looks like a PDF"
		);
		expect(explain('signature_not_allowed', 'pdf')).toBe(
			"Its bytes aren't a picture or a video; it looks like pdf"
		);
	});

	it('says why each kind of skipped file was refused in plain words', () => {
		expect(explain('signature_not_allowed', 'unrecognized', 'clips/noise.mp4')).toBe(
			"Its bytes aren't a picture or a video"
		);
		expect(explain('empty', null, 'clips/nothing.mp4')).toBe('An empty file');
		expect(explain('removed_from_sift', null, 'shoot.zip/01.png')).toBe(
			"Removed from Sift; it's inside a ZIP file"
		);
		expect(explain('signature_not_allowed', 'text/html', 'clips/error.mov')).toBe(
			'A web page saved as a video'
		);
		expect(explain('signature_not_allowed', 'text/html', 'photos/error.jpg')).toBe(
			'A web page saved as a picture'
		);
	});

	it('leaves the sentence alone when nothing was worked out about the bytes', () => {
		expect(explain('not_decodable', null)).toBe("It wouldn't open");
	});

	it('makes a reason it has never seen readable rather than printing the rule name', () => {
		/* A reason added to the gate and not to the list is the ordinary way this goes stale, and the
		   screen showing `some_new_rule` is worse than it showing "some new rule". */
		expect(explain('some_new_rule', null)).toBe('Some new rule');
	});

	it('says a file with no note is unexplained once, not twice', () => {
		/* Composing the unknowns would produce "Sift did not record why, from somewhere Sift did not
		   record", which says the same thing twice and reads like a fault. */
		const line = aboutQuarantined({ ...MOVED, explained: false }, 'last Tuesday');

		expect(line).toBe("Sift didn't record why this was quarantined — last Tuesday");
		expect(line).not.toContain('from somewhere');
	});

	it('says the reason, where it came from and when, for a file that has a note', () => {
		expect(aboutQuarantined(MOVED, 'last Tuesday')).toBe(
			"Its bytes aren't a picture or a video; it looks like pdf, from a download — last Tuesday"
		);
	});

	it('makes an origin it has never seen readable too', () => {
		expect(aboutQuarantined({ ...MOVED, origin: 'some_new_way' }, 'today')).toContain(
			'from some new way'
		);
	});
});
