/* The hover clip, for the two walls that are not the main grid.
 *
 * Search results and the inside of a collection lay their tiles out with the same justified rows
 * the library uses, but they draw `Tile` themselves, so without this they would get the still and
 * nothing else, and pointing at a video on either screen would show a frozen first frame on a
 * library where the same tile moves everywhere else.
 *
 * Two things here are worth a test rather than a reading. **Which tiles have a clip at all**, where
 * a wrong answer means either a still image asking for a preview that cannot exist or a GIF quietly
 * losing the one it has. And **what happens as the cursor crosses between two tiles**, because the
 * events arrive in an order that makes the obvious implementation wipe the tile just entered.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { canPreview, HoverPreviews } from '$lib/grid/hover-preview.svelte';

beforeEach(() => {
	vi.stubGlobal('URL', {
		createObjectURL: (blob: Blob) => `blob:${blob.size}`,
		revokeObjectURL: () => {}
	});
});

/** A fetch that answers with a clip, and records what was asked for. */
function clips(asked: string[]) {
	return vi.fn(async (source: string) => {
		asked.push(String(source));
		return { ok: true, blob: async () => new Blob(['clip']) } as unknown as Response;
	});
}

describe('which tiles have a clip at all', () => {
	it('a video does', () => {
		expect(canPreview({ media_type: 'video' })).toBe(true);
	});

	it('and so does a GIF', () => {
		/* It moves, so the import builds it a preview exactly as for a video. Reading "not a video"
		   as "no clip" is how a wall of GIFs goes still. */
		expect(canPreview({ media_type: 'gif' })).toBe(true);
	});

	it('a still image does not', () => {
		expect(canPreview({ media_type: 'image' })).toBe(false);
	});

	it('and a concealed file does not, whatever it is', () => {
		/* A concealed file serves nothing. Asking anyway is a refused request per tile, for ever. */
		expect(canPreview({ media_type: 'video', concealed: true })).toBe(false);
	});
});

describe('the cursor moving over a wall', () => {
	it('only the tile under the cursor is playing', () => {
		const previews = new HoverPreviews();

		previews.enter({ id: 'a' }, true, false);

		expect(previews.playing('a')).toBe(true);
		expect(previews.playing('b')).toBe(false);
	});

	it('leaving a tile stops it', () => {
		const previews = new HoverPreviews();
		previews.enter({ id: 'a' }, true, false);

		previews.enter({ id: 'a' }, false, false);

		expect(previews.playing('a')).toBe(false);
		expect(previews.hovered).toBeNull();
	});

	it('crossing from one tile to the next leaves the new one playing', () => {
		/* The pointer leaving A and entering B arrive in that order, so a leave that cleared
		   unconditionally would wipe the tile that had just been entered, and the wall would go
		   still every time the cursor moved between two tiles, which is most of the time. */
		const previews = new HoverPreviews();
		previews.enter({ id: 'a' }, true, false);

		previews.enter({ id: 'b' }, true, false);
		previews.enter({ id: 'a' }, false, false);

		expect(previews.playing('b')).toBe(true);
		expect(previews.playing('a')).toBe(false);
	});
});

describe('fetching the clip', () => {
	it('asks for the address the server actually serves clips on', async () => {
		/* The address is assembled HERE, out of an id, and a suite that stops at the payload cannot
		   see it. An address built as `/thumbnail` where the route is `/thumb` would break every
		   picture with every test green. */
		const asked: string[] = [];
		vi.stubGlobal('fetch', clips(asked));
		const previews = new HoverPreviews();

		previews.enter({ id: 'abc' }, true, true);
		await vi.waitFor(() => expect(previews.srcFor('abc')).toBeDefined());

		expect(asked).toEqual(['/api/assets/abc/preview']);
	});

	it('carries the token the server sent, so the clip can be kept without asking again', async () => {
		/* Without it the address is bare, the clip is revalidated on every visit, and nothing on
		   screen looks any different, which is exactly why it is asserted here. */
		const asked: string[] = [];
		vi.stubGlobal('fetch', clips(asked));
		const previews = new HoverPreviews();

		previews.enter({ id: 'abc', art: 'f3a1b2c4' }, true, true);
		await vi.waitFor(() => expect(previews.srcFor('abc')).toBeDefined());

		expect(asked).toEqual(['/api/assets/abc/preview?v=f3a1b2c4']);
	});

	it('a tile with no clip is pointed at without asking for one', async () => {
		const asked: string[] = [];
		vi.stubGlobal('fetch', clips(asked));
		const previews = new HoverPreviews();

		previews.enter({ id: 'still' }, true, false);
		await Promise.resolve();

		expect(asked).toEqual([]);
		// Nothing arrived, so the tile goes on showing its still rather than a gap.
		expect(previews.srcFor('still')).toBeUndefined();
		expect(previews.playing('still')).toBe(true);
	});

	it('hands nothing back until the clip has arrived', async () => {
		/* Undefined rather than an empty string, which is what makes the tile keep its still picture
		   instead of drawing a video element with nothing in it. */
		let answer: (value: Response) => void = () => {};
		vi.stubGlobal(
			'fetch',
			vi.fn(() => new Promise<Response>((resolve) => (answer = resolve)))
		);
		const previews = new HoverPreviews();

		previews.enter({ id: 'abc' }, true, true);
		await Promise.resolve();
		expect(previews.srcFor('abc')).toBeUndefined();

		answer({ ok: true, blob: async () => new Blob(['clip']) } as unknown as Response);
		await vi.waitFor(() => expect(previews.srcFor('abc')).toBeDefined());
	});

	it('a clip that is not built yet leaves the still in place', async () => {
		/* A video's preview does not exist until the job behind it finishes, so a tile pointed at
		   mid-scan answers 404. The tile keeps its still, which is a complete thing to look at. */
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => ({ ok: false }) as unknown as Response)
		);
		const previews = new HoverPreviews();

		previews.enter({ id: 'abc' }, true, true);
		await Promise.resolve();
		await Promise.resolve();

		expect(previews.srcFor('abc')).toBeUndefined();
	});

	it('gives every object url back when the screen goes', async () => {
		const revoked: string[] = [];
		vi.stubGlobal('URL', {
			createObjectURL: (blob: Blob) => `blob:${blob.size}`,
			revokeObjectURL: (url: string) => revoked.push(url)
		});
		vi.stubGlobal('fetch', clips([]));
		const previews = new HoverPreviews();
		previews.enter({ id: 'a' }, true, true);
		await vi.waitFor(() => expect(previews.srcFor('a')).toBeDefined());

		previews.dispose();

		expect(revoked).toHaveLength(1);
		expect(previews.hovered).toBeNull();
		expect(previews.playing('a')).toBe(false);
		expect(previews.srcFor('a')).toBeUndefined();
	});
});
