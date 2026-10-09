import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/client';
import {
	confirmMatches,
	identifiedForPerson,
	faceGroups,
	facesOf,
	fetchModels,
	formatRange,
	cropUrl,
	faceCoverUrl,
	BLANK_PICTURE,
	blankOnRefusal,
	knownPeople
} from '$lib/people/faces.svelte';

/* The client side of recognizing faces. Two things are worth testing here and neither is about
 * drawing anything. */

const get = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());
vi.mock('$lib/api/client', async () => {
	const actual = await vi.importActual<typeof import('$lib/api/client')>('$lib/api/client');
	return { ...actual, api: { get, post } };
});

beforeEach(() => {
	get.mockReset();
	post.mockReset();
});

describe('when the feature is not there to answer', () => {
	it('reports no faces rather than a failure', async () => {
		get.mockRejectedValue(new ApiError(409, 'That is already done.'));

		await expect(facesOf('a1')).resolves.toEqual([]);
		await expect(faceGroups('open')).resolves.toEqual({ groups: [], total: 0, offset: 0 });
	});

	it('treats a file it may not see the same way, because the block cannot say otherwise', async () => {
		get.mockRejectedValue(new ApiError(404, 'Not found.'));

		await expect(facesOf('a1')).resolves.toEqual([]);
	});

	it('lets a refusal that is not about faces through', async () => {
		// A session that ended has to reach the client's own handler, or somebody sits looking at
		// an app that has quietly stopped working.
		get.mockRejectedValue(new ApiError(401, 'Please sign in.'));

		await expect(facesOf('a1')).rejects.toBeInstanceOf(ApiError);
	});
});

describe('who Sift can already recognize', () => {
	it("says recognition is off in the server's own words, rather than reporting a fault", async () => {
		get.mockRejectedValue(new ApiError(409, 'Face recognition is turned off.'));

		const answer = await knownPeople('ka');
		expect(answer.items).toEqual([]);
		expect(answer.total).toBe(0);
		expect(answer.declined).toBe('Face recognition is turned off.');
	});

	it('lets any other refusal through, so a fault still reads as one', async () => {
		get.mockRejectedValue(new ApiError(401, 'Please sign in.'));

		await expect(knownPeople('ka')).rejects.toBeInstanceOf(ApiError);
	});
});

describe('what comes back', () => {
	it('hands on an unnamed face without trying to name it', async () => {
		get.mockResolvedValue([
			{
				track_id: 't1',
				asset_id: 'a1',
				started_ms: 0,
				ended_ms: 0,
				person_id: null,
				person_name: null,
				confidence: null,
				attribution: null
			}
		]);

		const faces = await facesOf('a1');

		expect(faces[0].person_name).toBeNull();
		expect(
			faces[0].person_id,
			'the id is withheld with the name, never one without the other'
		).toBeNull();
	});

	it('reports the total the server gave rather than counting the rows', async () => {
		// The two differ whenever a page is smaller than the set.
		get.mockResolvedValue({ items: [{ track_id: 't1' }], total: 9 });

		await expect(identifiedForPerson('p1')).resolves.toMatchObject({ total: 9 });
	});
});

describe('a face crop address', () => {
	it('escapes the id rather than pasting it into a path', () => {
		expect(cropUrl({ track_id: 'a/b' })).toBe('/api/faces/a%2Fb/crop');
	});

	it('carries the token the server sent, so the picture can be kept without asking', () => {
		/* Without it the address never moves, so a face already in the browser would go on being
		   shown after the person in it was hidden: no request, and so no check. */
		expect(cropUrl({ track_id: 'abc', art: 'f3a1b2c4' })).toBe('/api/faces/abc/crop?v=f3a1b2c4');
	});

	it('is left bare when the server sent no token', () => {
		/* A bare address is a picture that is re-checked on every use. Slower, never wrong. */
		expect(cropUrl({ track_id: 'abc', art: null })).toBe('/api/faces/abc/crop');
	});

	it('is the hidden mark, and not an address at all, for a face the vault is holding', () => {
		/* Decided here rather than at each of the nine screens that draw one. */
		const drawn = cropUrl({ track_id: 'abc', art: 'f3a1b2c4', locked: true });

		expect(drawn.startsWith('data:image/svg+xml')).toBe(true);
		expect(drawn).not.toContain('/api/faces');
	});
});

describe('a face cover address', () => {
	it('carries the token too', () => {
		expect(faceCoverUrl('abc', 'f3a1b2c4')).toBe('/api/faces/abc/cover?v=f3a1b2c4');
	});

	it('and is left bare without one', () => {
		expect(faceCoverUrl('abc')).toBe('/api/faces/abc/cover');
	});
});

describe('a time range, as somebody reads it', () => {
	it('shows a still as one moment rather than as a range of zero length', () => {
		expect(formatRange(0, 0)).toBe('0:00');
		expect(formatRange(65_000, 65_000)).toBe('1:05');
	});

	it('shows a range when there is one', () => {
		expect(formatRange(5_000, 12_000)).toBe('0:05 – 0:12');
	});
});

describe('fetching the models', () => {
	it('hands back the job doing it rather than waiting for the download', async () => {
		post.mockResolvedValue({ job_id: 'j1' });

		await expect(fetchModels()).resolves.toBe('j1');
		expect(post).toHaveBeenCalledWith('/faces/weights/fetch', {});
	});

	it('does not swallow a refusal, because starting a download either happened or did not', async () => {
		// Unlike every read above: a screen that reported "nothing to show" here would leave
		// somebody pressing a button that silently does nothing.
		post.mockRejectedValue(new ApiError(409, 'That is already done.'));

		await expect(fetchModels()).rejects.toBeInstanceOf(ApiError);
	});
});

describe('agreeing with every match Sift made for one person', () => {
	it('sends no list of faces, because the server already holds one', async () => {
		post.mockResolvedValue({ confirmed: 344, references: 331 });

		await expect(confirmMatches('p 1')).resolves.toEqual({ confirmed: 344, references: 331 });
		// The whole tab is named as a scope, not spelt out as faces: the server holds the list.
		expect(post).toHaveBeenCalledWith('/faces/people/p%201/confirm-matches', {
			body: { scope: 'all' }
		});
	});

	it('sends the faces of a page or a pick, because those are what somebody saw', async () => {
		post.mockResolvedValue({ confirmed: 2, references: 2 });

		await confirmMatches('p1', { scope: 'picked', track_ids: ['t1', 't2'] });
		expect(post).toHaveBeenCalledWith('/faces/people/p1/confirm-matches', {
			body: { scope: 'picked', track_ids: ['t1', 't2'] }
		});
	});

	it('does not swallow a refusal, because this writes', async () => {
		// The reads above answer "nothing to show" on a refusal.
		post.mockRejectedValue(new ApiError(409, 'That is already done.'));

		await expect(confirmMatches('p1')).rejects.toBeInstanceOf(ApiError);
	});
});

describe('a face on a file in the vault', () => {
	it('is drawn as the hidden mark instead of asking for a crop the server refuses', () => {
		// Decided in one place because nine screens draw a crop, and the server answers 404 for
		// this one: a screen that asked anyway would draw the browser's torn-page glyph, which
		// reads as Sift being broken rather than as something being kept back.
		const drawn = cropUrl({ track_id: 't1', art: 'v1', locked: true });

		expect(drawn.startsWith('data:image/svg+xml')).toBe(true);
		expect(drawn).not.toContain('t1');
	});

	it('is the ordinary address again once the vault is open', () => {
		expect(cropUrl({ track_id: 't1', art: 'v1', locked: false })).toBe('/api/faces/t1/crop?v=v1');
	});
});

describe('a refused picture', () => {
	it('is blanked, never marked hidden: only the server may say a thing is kept back', () => {
		const picture = document.createElement('img');
		picture.src = 'http://localhost/api/assets/a1/thumb';
		blankOnRefusal({ currentTarget: picture } as unknown as Event);
		expect(picture.src).toBe(BLANK_PICTURE);
		expect(picture.src).not.toContain('svg');
	});

	it('is swapped once, so a refusal of the blank itself is not a loop', () => {
		const picture = document.createElement('img');
		picture.src = BLANK_PICTURE;
		const before = picture.src;
		blankOnRefusal({ currentTarget: picture } as unknown as Event);
		expect(picture.src).toBe(before);
	});
});
