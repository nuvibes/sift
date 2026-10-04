import { afterEach, describe, expect, it, vi } from 'vitest';
import { UNREACHABLE } from '$lib/shell/unreachable';
import { Picker, type Listing } from './picker.svelte';

/* Walking the picker.
 *
 * What is worth testing here is the walking, not the drawing: which path gets asked for when
 * somebody clicks a folder or a crumb, what is selected at each point, and what a failed request
 * leaves on screen. The server decides what is inside a folder and proves the path is allowed:
 * that is tested where it happens, against a real filesystem, because it is the part that matters.
 */

const originalFetch = globalThis.fetch;

function listing(over: Partial<Listing> = {}): Listing {
	return {
		path: '/media',
		entries: [
			{ name: 'library', path: '/media/library' },
			{ name: 'tv', path: '/media/tv' }
		],
		breadcrumb: [{ name: 'media', path: '/media' }],
		file_count: 0,
		nothing_granted: false,
		writable: false,
		read_only_mount: true,
		...over
	};
}

/** Answers every browse request with the listing for whichever path was asked for. */
function serve(byPath: Record<string, Listing>, fallback = listing()) {
	const mock = vi.fn(async (url: URL | RequestInfo): Promise<Response> => {
		// The empty string is the request with NO path: the list of granted folders, which is what
		// the server answers when it is asked for nothing. Keyed apart from '/media' on purpose:
		// mapping a no-path request onto a real folder's key would make the fallback unreachable.
		const asked = new URL(String(url), 'http://sift.test').searchParams.get('path') ?? '';
		return new Response(JSON.stringify(byPath[asked] ?? fallback), {
			status: 200,
			headers: { 'content-type': 'application/json' }
		});
	});
	globalThis.fetch = mock as unknown as typeof fetch;
	return mock;
}

/** The `path` parameter each browse request carried, in order. */
function askedFor(mock: ReturnType<typeof vi.fn>): (string | null)[] {
	return mock.mock.calls.map((call) =>
		new URL(String(call[0]), 'http://sift.test').searchParams.get('path')
	);
}

afterEach(() => {
	globalThis.fetch = originalFetch;
});

describe('opening', () => {
	it('asks for the media area when it is given nowhere in particular', async () => {
		const fetched = serve({});
		const picker = new Picker();

		await picker.open();

		expect(askedFor(fetched)).toEqual([null]);
		expect(picker.entries.map((entry) => entry.name)).toEqual(['library', 'tv']);
		expect(picker.loading).toBe(false);
	});

	it('asks for exactly the path it was handed', async () => {
		const fetched = serve({
			'/media/library': listing({
				path: '/media/library',
				entries: [{ name: 'holidays', path: '/media/library/holidays' }],
				breadcrumb: [
					{ name: 'media', path: '/media' },
					{ name: 'library', path: '/media/library' }
				]
			})
		});
		const picker = new Picker();

		await picker.open('/media/library');

		expect(askedFor(fetched)).toEqual(['/media/library']);
		expect(picker.entries.map((entry) => entry.name)).toEqual(['holidays']);
	});

	it('escapes a path so a folder with a space or a hash in its name still opens', async () => {
		const fetched = serve({});
		const picker = new Picker();

		await picker.open('/media/my videos #2');

		// Read back off the query string, which is the thing that would have broken.
		expect(askedFor(fetched)).toEqual(['/media/my videos #2']);
	});
});

describe('what is selected', () => {
	it('is nothing before anything has loaded', () => {
		expect(new Picker().selected).toBeNull();
	});

	it('is the folder being looked at, so entering and choosing are one click', async () => {
		serve({
			'/media/library': listing({
				breadcrumb: [
					{ name: 'media', path: '/media' },
					{ name: 'library', path: '/media/library' }
				]
			})
		});
		const picker = new Picker();

		await picker.open('/media/library');

		expect(picker.selected).toEqual({ name: 'library', path: '/media/library' });
	});
});

describe('going back', () => {
	it('has nowhere to go at the list of granted folders', async () => {
		/* The top of the picker is that list, and the server answers it with no breadcrumb at all:
		 * it is not a folder, so it has no parent and nothing above it to draw. */
		serve({}, listing({ path: '', breadcrumb: [], entries: [{ name: 'media', path: '/media' }] }));
		const picker = new Picker();

		await picker.open();

		expect(picker.canGoUp).toBe(false);
		expect(picker.atTopLevel).toBe(true);
	});

	it('goes from the outermost granted folder back to the list of them', async () => {
		/* One crumb means standing IN a granted folder, whose parent is not a folder Sift may look
		 * in, so back means the request with no path at all, not a parent crumb. Sending the
		 * parent would ask to browse above the grant, which the server refuses. */
		const top = listing({ path: '', breadcrumb: [], entries: [{ name: 'media', path: '/media' }] });
		const inside = listing({ path: '/media', breadcrumb: [{ name: 'media', path: '/media' }] });
		const fetched = serve({ '/media': inside }, top);
		const picker = new Picker();
		await picker.open('/media');
		expect(picker.canGoUp).toBe(true);

		await picker.up();

		expect(picker.atTopLevel).toBe(true);
		// No `path` at all, which is how the list of granted folders is asked for.
		expect(askedFor(fetched).at(-1)).toBeNull();
	});

	/* A granted folder can be indexed as it stands. Somebody who handed over `D:\Media` because
	 * that is where their media is should not have to go a level deeper to say so.
	 */
	it('lets the granted folder itself be chosen', async () => {
		const inside = listing({ path: '/media', breadcrumb: [{ name: 'media', path: '/media' }] });
		serve({ '/media': inside });
		const picker = new Picker();

		await picker.open('/media');

		expect(picker.atTopLevel).toBe(false);
		expect(picker.selected).toEqual({ name: 'media', path: '/media' });
	});

	it('goes to the crumb one level up, not to the top', async () => {
		const deep = listing({
			path: '/media/library/holidays',
			breadcrumb: [
				{ name: 'media', path: '/media' },
				{ name: 'library', path: '/media/library' },
				{ name: 'holidays', path: '/media/library/holidays' }
			]
		});
		const fetched = serve({ '/media/library/holidays': deep });
		const picker = new Picker();
		await picker.open('/media/library/holidays');

		await picker.up();

		expect(askedFor(fetched).at(-1)).toBe('/media/library');
	});

	it('does nothing at the list of granted folders rather than asking for something above it', () => {
		return (async () => {
			const fetched = serve({}, listing({ path: '', breadcrumb: [] }));
			const picker = new Picker();
			await picker.open();

			await picker.up();

			expect(fetched).toHaveBeenCalledTimes(1);
		})();
	});
});

describe('nothing granted', () => {
	it('is carried through, because the screen answers it with the wizard', async () => {
		serve({}, listing({ entries: [], nothing_granted: true }));
		const picker = new Picker();

		await picker.open();

		expect(picker.nothingGranted).toBe(true);
	});

	it('is not set for an empty folder inside the media area', async () => {
		serve({}, listing({ entries: [], nothing_granted: false }));
		const picker = new Picker();

		await picker.open();

		expect(picker.nothingGranted).toBe(false);
		expect(picker.entries).toEqual([]);
	});
});

describe('what it says about writing', () => {
	it('carries both answers, because they need different sentences', async () => {
		serve({}, listing({ writable: false, read_only_mount: true }));
		const picker = new Picker();

		await picker.open();

		expect(picker.writable).toBe(false);
		expect(picker.readOnlyMount).toBe(true);
	});

	it('reports a folder Sift can write to', async () => {
		serve({}, listing({ writable: true, read_only_mount: false }));
		const picker = new Picker();

		await picker.open();

		expect(picker.writable).toBe(true);
	});
});

describe('when the server refuses', () => {
	it('keeps where somebody was rather than dropping them at the top', async () => {
		const deep = listing({
			path: '/media/library',
			entries: [{ name: 'holidays', path: '/media/library/holidays' }],
			breadcrumb: [
				{ name: 'media', path: '/media' },
				{ name: 'library', path: '/media/library' }
			]
		});
		serve({ '/media/library': deep });
		const picker = new Picker();
		await picker.open('/media/library');

		globalThis.fetch = vi.fn(
			async () =>
				new Response(JSON.stringify({ detail: 'That folder is not one Sift has been given.' }), {
					status: 400,
					headers: { 'content-type': 'application/json' }
				})
		) as unknown as typeof fetch;
		await picker.open('/etc');

		expect(picker.failed).toBe('That folder is not one Sift has been given.');
		expect(picker.entries.map((entry) => entry.name)).toEqual(['holidays']);
		expect(picker.selected?.path).toBe('/media/library');
	});

	it('stops loading, so the picker does not sit greyed out forever', async () => {
		globalThis.fetch = vi.fn(async () => {
			throw new TypeError('network');
		}) as unknown as typeof fetch;
		const picker = new Picker();

		await picker.open();

		expect(picker.loading).toBe(false);
		expect(picker.failed).toBe(UNREACHABLE);
	});

	it('clears an old failure once a request works again', async () => {
		globalThis.fetch = vi.fn(async () => {
			throw new TypeError('network');
		}) as unknown as typeof fetch;
		const picker = new Picker();
		await picker.open();

		serve({});
		await picker.open();

		expect(picker.failed).toBeNull();
	});
});

describe('what is in the folder that the list does not show', () => {
	it('carries the file count, so a folder of media is not drawn as an empty box', async () => {
		/* The picker lists folders and never files, which is what stops it being a way to read
		 * somebody's filenames, and which would make a folder full of media look exactly like
		 * an empty one. The count is the whole of the answer and it has to reach the screen.
		 */
		serve({}, listing({ entries: [], file_count: 42 }));
		const picker = new Picker();

		await picker.open();

		expect(picker.fileCount).toBe(42);
	});

	it('is zero for a folder with nothing in it', async () => {
		serve({}, listing({ entries: [], file_count: 0 }));
		const picker = new Picker();

		await picker.open();

		expect(picker.fileCount).toBe(0);
	});
});

/* Which set of folders is being walked.
 *
 * The scope is on every request rather than only the first, and that is the whole of what could go
 * wrong here: one call that forgot it is a folder that lists on the way in and is refused on the way
 * back out, which reads as the server losing a folder somebody is standing in.
 */
describe('the scope', () => {
	/** Every scope a run of requests asked for, in order. */
	function scopesFrom(mock: ReturnType<typeof serve>): (string | null)[] {
		return mock.mock.calls.map((call) =>
			new URL(String(call[0]), 'http://sift.test').searchParams.get('scope')
		);
	}

	it('asks for the granted folders unless it is told otherwise', async () => {
		const mock = serve({});
		const picker = new Picker();

		await picker.open();

		expect(scopesFrom(mock)).toEqual(['granted']);
	});

	it('carries the machine scope down a walk and back up again', async () => {
		const mock = serve({
			'': listing({ path: '', entries: [{ name: 'C:', path: 'C:\\' }], breadcrumb: [] }),
			'C:\\': listing({ path: 'C:\\', breadcrumb: [{ name: 'C:', path: 'C:\\' }] })
		});
		const picker = new Picker();

		await picker.look('machine');
		await picker.open('C:\\');
		await picker.up();

		expect(scopesFrom(mock)).toEqual(['machine', 'machine', 'machine']);
	});

	/* Ticking a folder asks about that one folder, only to learn whether Sift may write in it.
	   Without the scope that request is answered against the granted list, so a folder picked off
	   the machine comes back refused and is silently recorded as read-only. */
	it('asks about a ticked folder in the scope it was ticked in', async () => {
		const mock = serve({});
		const picker = new Picker();
		await picker.look('machine');

		await picker.choose({ name: 'clips', path: 'D:\\clips' });

		expect(scopesFrom(mock).at(-1)).toBe('machine');
	});

	/*
	 * A FAILED WRITE-CHECK IS NOT A READ-ONLY FOLDER, and drawing them the same way would state a
	 * fact about somebody's disk that Sift did not have: a greyed switch and "handed to Sift as
	 * read-only, so Sift cannot change anything in it" over a folder with full write access, while
	 * a cancelled check was all that happened. Read-only stays the safe answer to an unknown; what
	 * changes is that the screen says it could not tell rather than asserting a no.
	 */
	it('records a check that never answered as unknown, not as read-only', async () => {
		globalThis.fetch = vi.fn(async () => {
			throw new TypeError('Failed to fetch');
		}) as unknown as typeof fetch;
		const picker = new Picker();

		await picker.choose({ name: 'clips', path: 'D:\\clips' });

		expect(picker.chosen).toHaveLength(1);
		expect(picker.chosen[0].writable).toBe(false);
		expect(picker.chosen[0].checked).toBe(false);
	});

	/*
	 * THE TICK IS THE PRESS, NOT THE ANSWER TO IT.
	 *
	 * A row added only once the write-check came back would leave the box empty for the length of a
	 * request: on a network share, the one storage this check is slow on, long enough to look
	 * like a control that does nothing: a completed click leaving the box reading
	 * `aria-checked="false"`. What somebody does then is press it again, and a second press is an
	 * untick.
	 *
	 * Held open deliberately here rather than measured with a timer: the request is still out, and
	 * the folder is already ticked.
	 */
	it('ticks the folder the moment it is pressed, before the write-check answers', async () => {
		// A function from the start, not a null: the type checker cannot see the assignment inside the
		// closure below, and narrows a `| null` to `null` at the call.
		let answer: () => void = () => {};
		globalThis.fetch = vi.fn(async () => {
			await new Promise<void>((settle) => (answer = settle));
			return new Response(JSON.stringify(listing({ writable: true })), {
				status: 200,
				headers: { 'content-type': 'application/json' }
			});
		}) as unknown as typeof fetch;
		const picker = new Picker();

		const ticking = picker.choose({ name: 'clips', path: 'D:\\clips' });
		await Promise.resolve();

		expect(picker.isChosen('D:\\clips')).toBe(true);
		// Nothing is claimed about the folder until the question has an answer. See `Chosen.checked`.
		expect(picker.chosen[0].checked).toBe(false);

		answer();
		await ticking;

		expect(picker.chosen).toHaveLength(1);
		expect(picker.chosen[0].writable).toBe(true);
		expect(picker.chosen[0].checked).toBe(true);
	});

	it('records a folder the server said is read-only as checked', async () => {
		serve({}, listing({ writable: false, read_only_mount: true }));
		const picker = new Picker();

		await picker.choose({ name: 'clips', path: 'D:\\clips' });

		expect(picker.chosen[0].writable).toBe(false);
		expect(picker.chosen[0].checked).toBe(true);
	});

	/* The two sets do not overlap in any way a breadcrumb could survive, so switching goes back to
	   the top. Left where it was, the trail would name folders the new scope may refuse to list. */
	it('goes back to the top when the scope changes', async () => {
		const mock = serve({});
		const picker = new Picker();
		await picker.open('/media/library');
		mock.mockClear();

		await picker.look('machine');

		expect(mock).toHaveBeenCalledOnce();
		expect(
			new URL(String(mock.mock.calls[0][0]), 'http://sift.test').searchParams.get('path')
		).toBeNull();
	});

	it('does nothing when asked for the scope it is already in', async () => {
		const mock = serve({});
		const picker = new Picker();
		await picker.open();
		mock.mockClear();

		await picker.look('granted');

		expect(mock).not.toHaveBeenCalled();
	});
});
