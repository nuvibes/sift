import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, Unreachable, api, request, setCsrfToken } from './client';
import { UNREACHABLE } from '$lib/shell/unreachable';
import type { components } from '$lib/api/schema';

/* The wrapper every request goes through. Worth testing directly rather than through a screen:
 * these are the rules that hold for all of them, and a screen only ever exercises one.
 */

const originalFetch = globalThis.fetch;

/** The arguments the wrapper handed to fetch, which is what these tests are actually about. */
type FetchCall = [URL | RequestInfo, RequestInit];

function respondWith(status: number, body: unknown = {}, extra: Record<string, string> = {}) {
	const mock = vi.fn(async (): Promise<Response> =>
		status === 204
			? new Response(null, { status })
			: new Response(JSON.stringify(body), {
					status,
					headers: { 'content-type': 'application/json', ...extra }
				})
	);
	globalThis.fetch = mock as unknown as typeof fetch;
	return {
		/** The nth call, typed. Fails loudly rather than returning undefined into an assertion. */
		call(index = 0): FetchCall {
			const calls = mock.mock.calls as unknown as FetchCall[];
			if (!calls[index]) throw new Error(`fetch was not called ${index + 1} time(s)`);
			return calls[index];
		},
		headers(index = 0): Record<string, string> {
			return (this.call(index)[1].headers ?? {}) as Record<string, string>;
		}
	};
}

/** The error a request threw, as an ApiError. Keeps `unknown` out of every assertion below. */
async function failure(promise: Promise<unknown>): Promise<ApiError> {
	try {
		await promise;
	} catch (error) {
		if (error instanceof ApiError) return error;
		throw error;
	}
	throw new Error('the request was expected to fail and did not');
}

/** Where the wrapper sent the browser, if it sent it anywhere. */
let wentTo: string[] = [];

function standingOn(path: string) {
	wentTo = [];
	// Written out rather than spread from a URL: a URL's properties live on its prototype, so a
	// spread of one is an empty object and `origin` comes back undefined, which fails every
	// request in this file with "Invalid URL" and says nothing about why.
	vi.stubGlobal('location', {
		origin: 'http://localhost:5171',
		href: `http://localhost:5171${path}`,
		pathname: path,
		replace: (to: string) => wentTo.push(to)
	});
}

beforeEach(() => {
	setCsrfToken(null);
	standingOn('/browse');
});

afterEach(() => {
	globalThis.fetch = originalFetch;
	vi.unstubAllGlobals();
});

/* A real address, because the client only accepts real ones. Which one is beside the point in
   the three tests below: they are about what a refusal turns into, and the request never reaches
   a server. It stays one name so that an endpoint being renamed changes it in one place. */
const ANY_PATH = '/assets';

describe('the CSRF token', () => {
	it('rides on a request that changes something', async () => {
		const fetched = respondWith(200);
		setCsrfToken('a-token');

		await api.post('/auth/logout');

		expect(fetched.headers()['x-csrf-token']).toBe('a-token');
	});

	it.each(['PUT', 'DELETE'])('rides on %s too', async (method) => {
		const fetched = respondWith(200);
		setCsrfToken('a-token');

		await request(method, '/settings');

		expect(fetched.headers()['x-csrf-token']).toBe('a-token');
	});

	it('stays off a plain read', async () => {
		const fetched = respondWith(200);
		setCsrfToken('a-token');

		await api.get('/auth/me');

		expect(fetched.headers()['x-csrf-token']).toBeUndefined();
	});
});

describe('the session', () => {
	it('travels with every request, and only to this origin', async () => {
		const fetched = respondWith(200);

		await api.get('/auth/me');

		expect(fetched.call()[1].credentials).toBe('same-origin');
	});

	it('goes to the API under its own prefix', async () => {
		const fetched = respondWith(200);

		await api.get('/auth/me');

		expect(String(fetched.call()[0])).toBe('http://localhost:5171/api/auth/me');
	});
});

describe('a parameter', () => {
	it('is escaped here, so no caller has to remember to', async () => {
		// A name with an ampersand in it is ordinary. Pasted into an address unescaped it ends the
		// parameter and the rest of the name becomes something else: a request that quietly asks
		// a different question. Against a folder name, a `#` asks for another folder.
		const fetched = respondWith(200);

		await api.get('/auth/me', { query: { term: 'Quillhouse & Sons #2' } });

		expect(String(fetched.call()[0])).toBe(
			'http://localhost:5171/api/auth/me?term=Quillhouse+%26+Sons+%232'
		);
	});

	it('is left out when there is nothing to say, rather than sent empty', async () => {
		// An absent value and an empty one are different questions. A filter nobody set must not
		// arrive as a filter set to nothing.
		const fetched = respondWith(200);

		await api.get('/auth/me', { query: { term: undefined, offset: 0 } });

		expect(String(fetched.call()[0])).toBe('http://localhost:5171/api/auth/me?offset=0');
	});

	it('sends every value of a list, as a repeated parameter', async () => {
		/* Which is what every list route reads as a set of values for one filter. With `set` and
		   a single value, a caller holding two would have to pick one or join them with a
		   separator this file knows nothing about. And `set` replaces, so a list of three
		   would arrive as its last member and ask a much wider question with nothing on screen
		   to reveal it. */
		const fetched = respondWith(200);

		await api.get('/auth/me', { query: { hair_color: ['BLONDE', 'RED'], limit: 24 } });

		expect(String(fetched.call()[0])).toBe(
			'http://localhost:5171/api/auth/me?hair_color=BLONDE&hair_color=RED&limit=24'
		);
	});

	it('sends nothing at all for an empty list, which narrows nothing', async () => {
		const fetched = respondWith(200);

		await api.get('/auth/me', { query: { hair_color: [], limit: 24 } });

		expect(String(fetched.call()[0])).toBe('http://localhost:5171/api/auth/me?limit=24');
	});
});

describe('what a failure says', () => {
	/* The one that matters.
	 *
	 * The server answers an identical 404 whether a thing is missing or is there and not yours, so
	 * that asking cannot be a way of finding out. The client can give that away without touching the
	 * server: "you do not have access to this" tells the person exactly what the 404 was refusing to.
	 * It is a friendlier message and it is the leak.
	 */
	it('says a thing was not found, and never that it was forbidden', async () => {
		respondWith(404, { detail: 'Not found.' });

		const error = await failure(api.get('/assets/01HX'));

		expect(error.status).toBe(404);
		expect(error.message).toBe('Not found.');

		for (const leak of ['access', 'permission', 'forbidden', 'allowed', 'yours', 'denied']) {
			expect(error.message.toLowerCase()).not.toContain(leak);
		}
	});

	it('does not read the server body into the message either', async () => {
		// A 404 whose body says too much must not reach the screen through this. The message comes
		// from the status, not from what came back.
		respondWith(404, { detail: 'asset 01HX exists but belongs to someone else' });

		const error = await failure(api.get('/assets/01HX'));

		expect(error.message).toBe('Not found.');
		expect(error.message).not.toContain('someone else');
	});

	it.each([
		[401, 'Please sign in.'],
		[403, "That isn't allowed."],
		[500, 'Something went wrong.']
	])('turns %i into a sentence', async (status, expected) => {
		respondWith(status);

		const error = await failure(api.get(ANY_PATH));

		expect(error.message).toBe(expected);
	});
});

/** A 401 the server marked as "there is no session", which is the only kind that redirects. */
function noSession() {
	return respondWith(401, {}, { 'www-authenticate': 'Session' });
}

describe('a session that ended underneath somebody', () => {
	/*
	 * An account can be blocked, reset or expired while its owner is looking at a page that was
	 * drawn while it was still theirs. Nothing on that page is wrong (it was true when it loaded),
	 * and without this, everything they touched would come back "Please sign in" with nothing on
	 * screen that took them anywhere, which reads as the app being broken rather than as being
	 * signed out.
	 */
	it('sends the browser to the sign-in screen', async () => {
		noSession();

		await failure(api.get('/assets'));

		expect(wentTo).toEqual(['/login']);
	});

	it('leaves a wrong credential alone, wherever it was checked', async () => {
		/* Signing in with the wrong password is a 401. So is mistyping your current password on the
		 * change-password form, and so is a wrong PIN. None of them is a session ending, and none of
		 * them carries the marker, which is why this is the server's call rather than a list of
		 * addresses kept in step by hand. */
		respondWith(401);

		await failure(api.post('/auth/login', { body: { username: 'kate', password: 'no' } }));
		await failure(api.post('/auth/password', { body: {} }));
		await failure(api.post('/vault/unlock', { body: { pin: '0000' } }));

		expect(wentTo).toEqual([]);
	});

	it('does not do it for a visitor who was never signed in', async () => {
		// `/auth/me` is asked on every page load, including on the sign-in screen. It answers 401
		// for everybody who has not signed in yet, and it does carry the marker, so the guard
		// below is what stops the sign-in screen navigating to itself.
		standingOn('/login');
		noSession();

		await failure(api.get('/auth/me'));

		expect(wentTo).toEqual([]);
	});

	/*
	 * THE SCREEN THAT CREATES THE ACCOUNT.
	 *
	 * `/login` and `/setup` are the same case: nobody is signed in on either, and `/auth/me`
	 * answers 401 with the session marker on both.
	 *
	 * It is a loop rather than a stray navigation. The root layout re-reads the session whenever
	 * the WINDOW takes focus (which in the desktop app includes clicking into a field), so an
	 * unguarded `/setup` would do a full page load to `/login` on every click on the account form,
	 * `/login` would send it back to `/setup` because no admin exists yet, and the half-typed form
	 * would come back empty.
	 */
	it('and does not do it on the screen that creates the first account', async () => {
		standingOn('/setup');
		noSession();

		await failure(api.get('/auth/me'));

		expect(wentTo).toEqual([]);
	});

	/* The locked screen takes the same list, so a 423 on an auth screen cannot bounce either. */
	it('and does not send an auth screen to the locked screen either', async () => {
		standingOn('/setup');
		respondWith(423, {}, { 'sift-locked': 'session' });

		await failure(api.get('/auth/me'));

		expect(wentTo).toEqual([]);
	});

	it('leaves an ordinary refusal alone', async () => {
		respondWith(403);

		await failure(api.get('/assets'));

		expect(wentTo).toEqual([]);
	});
});

describe("the server's own words", () => {
	/* Kept, and kept apart.
	 *
	 * Some refusals are written for the person reading them and are the whole product of the
	 * endpoint: "Sift is already watching that folder as part of Videos" says what to change, where
	 * "That request was not valid" leaves somebody holding a path and no idea what is wrong with it.
	 * So the words are carried: next to the one-liner, never instead of it, so that a screen has
	 * to ask for them and the default stays safe.
	 */
	it('are carried alongside the message, not inside it', async () => {
		respondWith(400, { detail: 'Sift is already watching that folder as part of Videos.' });

		const error = await failure(api.get('/library/roots'));

		expect(error.detail).toBe('Sift is already watching that folder as part of Videos.');
		expect(error.message).toBe("That request wasn't valid.");
	});

	it('are absent when the server sent none', async () => {
		respondWith(400, { something: 'else' });

		const error = await failure(api.get(ANY_PATH));

		expect(error.detail).toBeUndefined();
	});

	it('are absent when the body is not something this app sent', async () => {
		// A proxy, a load balancer, anything in front of the app that answers in HTML. Reading that
		// into a sentence somebody is shown is how a stack trace ends up on a screen.
		globalThis.fetch = vi.fn(
			async () => new Response('<html>502 Bad Gateway</html>', { status: 502 })
		) as unknown as typeof fetch;

		const error = await failure(api.get(ANY_PATH));

		expect(error.detail).toBeUndefined();
		expect(error.message).toBe('Something went wrong.');
	});

	it('are not a way around what a 404 refuses to say', async () => {
		// The detail exists for refusals somebody can act on. A 404's cannot be one: the whole point
		// of it is that it says nothing. Nothing here makes it say anything: a screen has to reach
		// for the detail deliberately, and no screen does that for a read.
		respondWith(404, { detail: 'asset 01HX exists but belongs to someone else' });

		const error = await failure(api.get('/assets/01HX'));

		expect(error.message).toBe('Not found.');
		expect(error.message).not.toContain('someone else');
	});
});

describe('the reply', () => {
	it('is parsed when there is one', async () => {
		respondWith(200, { id: '01HX' });
		await expect(api.get<components['schemas']['ViewerResponse']>('/auth/me')).resolves.toEqual({
			id: '01HX'
		});
	});

	it('is nothing when the server sends no content', async () => {
		respondWith(204);
		await expect(api.post('/auth/logout')).resolves.toBeUndefined();
	});
});

describe('a multipart body', () => {
	it('is sent as-is, without a content-type the browser has to write itself', async () => {
		const fetched = respondWith(202, { job_id: 'j1' });
		const form = new FormData();
		form.set('file', new File([new Uint8Array([1, 2])], 'clip.png', { type: 'image/png' }));

		await api.post('/capture/import/file', { body: form });

		const [, init] = fetched.call();
		expect(init.body).toBeInstanceOf(FormData);
		// The boundary lives in a header only the browser can write; setting it here breaks the parse.
		expect(fetched.headers()).not.toHaveProperty('content-type');
	});

	it('still carries the CSRF token', async () => {
		const fetched = respondWith(202, {});
		setCsrfToken('a-token');

		await api.post('/capture/import/file', { body: new FormData() });

		expect(fetched.headers()['x-csrf-token']).toBe('a-token');
	});
});

describe('a request that must outlive the page', () => {
	it('is marked keepalive when asked, and is not otherwise', async () => {
		// The player's last view-report fires while the tab is closing. Without this the browser
		// cancels the in-flight request and the view is never counted.
		const fetched = respondWith(204);

		await api.post('/assets/a1/view', { body: { watch_ms: 5 }, keepalive: true });
		expect(fetched.call(0)[1].keepalive).toBe(true);

		await api.post('/assets/a1/view', { body: { watch_ms: 5 } });
		expect(fetched.call(1)[1].keepalive).toBeUndefined();
	});
});

describe('a server that does not answer', () => {
	it('fails with the one sentence, whatever the browser said', async () => {
		// The browser's own words are "Failed to fetch", and a screen that draws a thrown message
		// as it stands would draw exactly that when Sift stops.
		globalThis.fetch = vi.fn(async () => {
			throw new TypeError('Failed to fetch');
		}) as unknown as typeof fetch;

		const thrown = await api.get('/assets').then(
			() => null,
			(error: unknown) => error
		);

		expect(thrown).toBeInstanceOf(Unreachable);
		expect((thrown as Error).message).toBe(UNREACHABLE);
	});

	it('hands a request the caller aborted back as the abort it was', async () => {
		const stop = new AbortController();
		stop.abort();
		globalThis.fetch = vi.fn(async () => {
			throw new DOMException('The operation was aborted.', 'AbortError');
		}) as unknown as typeof fetch;

		const thrown = await api.get('/assets', { signal: stop.signal }).then(
			() => null,
			(error: unknown) => error
		);

		expect(thrown).not.toBeInstanceOf(Unreachable);
		expect((thrown as Error).name).toBe('AbortError');
	});
});

/* A page's first draw asks for the same addresses from several places together: a second read of
   an address already on its way joins it rather than going again. */
describe('a read already on its way', () => {
	it('is joined, and each caller holds its own copy of the answer', async () => {
		respondWith(200, { folders: [{ name: 'Holidays' }] });

		const [first, second] = (await Promise.all([
			api.get('/library/folders'),
			api.get('/library/folders')
		])) as { folders: { name: string }[] }[];

		expect(globalThis.fetch).toHaveBeenCalledTimes(1);
		expect(second).toEqual(first);
		second.folders[0].name = 'changed';
		expect(first.folders[0].name).toBe('Holidays');
	});

	it('is asked again once the first answer has come back', async () => {
		respondWith(200, {});

		await api.get('/library/folders');
		await api.get('/library/folders');

		expect(globalThis.fetch).toHaveBeenCalledTimes(2);
	});

	it('is never joined by a write, another address, or a read with its own way to stop', async () => {
		respondWith(200, {});

		await Promise.all([
			api.get('/library/folders'),
			api.get('/library/folders', { query: { root: 'a' } }),
			api.post('/library/folders'),
			api.get('/library/folders', { signal: new AbortController().signal })
		]);

		expect(globalThis.fetch).toHaveBeenCalledTimes(4);
	});

	it('hands its refusal to every caller that joined it', async () => {
		respondWith(503);

		const both = await Promise.allSettled([api.get(ANY_PATH), api.get(ANY_PATH)]);

		expect(globalThis.fetch).toHaveBeenCalledTimes(1);
		expect(both.map((one) => one.status)).toEqual(['rejected', 'rejected']);
	});
});
