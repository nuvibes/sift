/* Adding a library folder, and the one thing about it that is not obvious. */

import { afterEach, expect, describe, it, vi } from 'vitest';
import { flushSync, tick } from 'svelte';

import { Library } from './library.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import { session, type Viewer } from '$lib/shell/session.svelte';

const originalFetch = globalThis.fetch;

afterEach(() => {
	globalThis.fetch = originalFetch;
	session.viewer = undefined;
});

/** Sign a viewer in with one role, which is all `load` reads of the session. */
function signedInAs(role: 'admin' | 'guest') {
	session.viewer = { role } as Viewer;
}

/** The addresses asked, in order, without their origin or query. */
function asked(mock: ReturnType<typeof vi.fn>): string[] {
	return mock.mock.calls.map(([url]) => new URL(String(url), 'http://sift.test').pathname);
}

/** The body of the one POST that was made. Fails loudly rather than returning undefined. */
function sentBody(mock: ReturnType<typeof vi.fn>): Record<string, unknown> {
	const call = mock.mock.calls.find(([, init]) => (init as RequestInit)?.method === 'POST');
	if (!call) throw new Error('no POST was made');
	return JSON.parse(String((call[1] as RequestInit).body));
}

function accepting() {
	const mock = vi.fn(
		async () =>
			new Response(JSON.stringify({ roots: [], folders: [], tree: [] }), {
				status: 200,
				headers: { 'content-type': 'application/json' }
			})
	);
	globalThis.fetch = mock as unknown as typeof fetch;
	return mock;
}

describe('adding a library folder', () => {
	it('reads it straight away unless told not to', async () => {
		const fetched = accepting();

		await new Library().addRoot('/media/photos');

		expect(sentBody(fetched).scan).toBe(true);
	});

	it('and can be told to add it without reading it yet', async () => {
		const fetched = accepting();

		await new Library().addRoot('/media/photos', false);

		expect(sentBody(fetched).scan).toBe(false);
	});
});

describe('reading the library from an effect', () => {
	/* THE REQUEST STORM, GUARDED AT THE STORE. `load` reads `roots` and `folders` to decide
	 * whether the screen has anything to show, and then writes both of them. */
	it('does not re-trigger the effect it was called from', async () => {
		const fetching = accepting();
		signedInAs('admin');
		const library = new Library();

		const stop = $effect.root(() => {
			$effect(() => {
				void library.load();
			});
		});
		flushSync();
		// Long enough for the two requests to land and write their answers back.
		for (let round = 0; round < 8; round += 1) {
			await tick();
			await Promise.resolve();
		}
		flushSync();
		await tick();

		// One pass is two reads: the tree, then the roots.
		expect(fetching.mock.calls.length).toBe(2);
		stop();
	});
});

describe('what adding a folder says out loud', () => {
	/* NO SCAN, NO SENTENCE ABOUT READING. Setup adds folders with the scan switched off, on
	 * purpose: somebody three questions from the end of a walk has not been told the machine is
	 * about to be taken. */
	it('says the folder was added when nothing was queued to read it', async () => {
		accepting();
		const library = new Library();

		await library.addRoot('D:\\media\\Holiday', false);

		const said = toasts.items.at(-1)?.message ?? '';
		expect(said).toContain('Sift has added Holiday');
		expect(said).not.toContain('reading');
	});

	it('and says it is reading when a scan really was queued', async () => {
		accepting();
		const library = new Library();

		await library.addRoot('D:\\media\\Holiday');

		expect(toasts.items.at(-1)?.message ?? '').toContain('Sift is reading Holiday');
	});

	/* THE FIRST FOLDER ON A DEVICE NEVER MEASURED IS NOT READ YET: the benchmark it queued goes
	 * first, so the toast says the server's sentence for that wait, the one the wall says too. */
	it('says the folder waits for the benchmark when the benchmark holds it', async () => {
		const held =
			'Your folder is added, and its files appear once this device has been benchmarked.';
		globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
			const body = String(url).includes('/performance/benchmark')
				? { state: 'running', job_id: 'job-1', said: 'Benchmarking', measured: false, held }
				: { roots: [], folders: [], tree: [] };
			return new Response(JSON.stringify(body), {
				status: 200,
				headers: { 'content-type': 'application/json' }
			});
		}) as unknown as typeof fetch;

		await new Library().addRoot('D:\\media\\Holiday');

		expect(toasts.items.at(-1)?.message ?? '').toBe(held.replace(/\.$/, ''));
	});
});

describe('what a guest asks when the library is read', () => {
	/* THE ROOTS ARE AN ADMIN'S QUESTION. The route answers an admin alone, so a guest's browser
	 * asking it is a refusal in the log and the console on every visit to Browse. */
	it('reads the folder tree and never the roots for a guest', async () => {
		const fetching = accepting();
		signedInAs('guest');
		const library = new Library();

		await library.load();

		expect(asked(fetching)).toEqual(['/api/library/folders']);
		expect(library.canManage).toBe(false);
		expect(library.roots).toEqual([]);
		expect(library.loading).toBe(false);
	});

	it('and reads both for an admin', async () => {
		const fetching = accepting();
		signedInAs('admin');
		const library = new Library();

		await library.load();

		expect(asked(fetching)).toEqual(['/api/library/folders', '/api/library/roots']);
		expect(library.canManage).toBe(true);
	});
});

describe('reading the library again', () => {
	/* The screen draws a skeleton while `loading` is true, and whatever stands inside the screen
	 * is made again when it comes back. */
	it('is loading until the first answer and not again, even with no folder yet', async () => {
		accepting();
		signedInAs('admin');
		const library = new Library();
		expect(library.loading).toBe(true);
		await library.load();
		expect(library.loading).toBe(false);

		const during: boolean[] = [];
		const stop = $effect.root(() => {
			$effect(() => {
				during.push(library.loading);
			});
		});
		flushSync();
		await library.load();
		flushSync();
		stop();

		expect(during).toEqual([false]);
	});
});
