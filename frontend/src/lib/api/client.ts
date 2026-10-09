import type { paths } from './schema';
import { CLIENT_HEADER, clientKind } from '$lib/shell/client-kind';
import { UNREACHABLE } from '$lib/shell/unreachable';

/* The one way the app talks to the server. */

export const API_PREFIX = '/api';

const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

/** Server paths, from the generated schema, so a typo is a build error, not a 404 at runtime. */
type WithoutPrefix<Path> = Path extends `${typeof API_PREFIX}${infer Rest}` ? Rest : never;

/** `/folders/{folder_id}/vault` -> `/folders/${string}/vault`. */
type WithValues<Path extends string> = Path extends `${infer Head}{${string}}${infer Tail}`
	? `${Head}${string}${WithValues<Tail>}`
	: Path;

export type ApiPath = WithValues<WithoutPrefix<keyof paths>>;

export class ApiError extends Error {
	readonly status: number;
	/** What the server said, when it said anything, and nothing is shown from it unless a screen
	 * asks. */
	readonly detail?: string;

	/** The field a refusal is about, where the route names one (the `Sift-Field` header): so a
	 * form can say the refusal beside that field rather than at its foot, far from what to
	 * change. */
	readonly field?: string;

	constructor(status: number, message: string, detail?: string, field?: string) {
		super(message);
		this.name = 'ApiError';
		this.status = status;
		this.detail = detail;
		this.field = field;
	}
}

/** The header a refusal names its field in. See `ApiError.field`. */
const FIELD_HEADER = 'Sift-Field';

/** The server did not answer at all: nothing came back to read a status from. */
export class Unreachable extends Error {
	constructor() {
		super(UNREACHABLE);
		this.name = 'Unreachable';
	}
}

/** Whether a failure actually means "there is no such thing", rather than "that did not work". */
export function isMissing(error: unknown): boolean {
	return error instanceof ApiError && error.status === 404;
}

/* The CSRF token, held in memory only. It is derived from the session cookie, so it is not a
 * second secret to look after: the server recomputes it per request and compares. */
let csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
	csrfToken = token;
}

/* What a failed request says. 404 is the interesting one, and it is the only one worth being
 * careful about. */
function messageFor(status: number): string {
	switch (status) {
		case 400:
			return "That request wasn't valid.";
		case 401:
			return 'Please sign in.';
		case 403:
			return "That isn't allowed.";
		case 404:
			return 'Not found.';
		case 409:
			return "That's already done.";
		case 422:
			return 'Some of those details were not valid.';
		case 429:
			return 'Too many attempts. Wait a moment and try again.';
		default:
			return status >= 500 ? 'Something went wrong.' : "That request didn't work.";
	}
}

/** The server's own words for a refusal, if it sent any and they are words. */
async function detailOf(response: Response): Promise<string | undefined> {
	try {
		const body: unknown = await response.json();
		const detail = (body as { detail?: unknown } | null)?.detail;
		return typeof detail === 'string' ? detail : undefined;
	} catch {
		return undefined;
	}
}

interface RequestOptions {
	body?: unknown;
	/* One value per name, or SEVERAL under one name. */
	query?: Record<string, string | number | boolean | readonly string[] | undefined>;
	signal?: AbortSignal;
	// Let the request finish even if the page is going away.
	keepalive?: boolean;
	/* Take the answer as bytes rather than parsing it. */
	asBlob?: boolean;
}

/* How many requests are on their way right now. */
let inFlight = 0;

/** Whether any request is still on its way. */
export function requestsInFlight(): boolean {
	return inFlight > 0;
}

/* Reads on their way, by address. */
const reading = new Map<string, { read: Promise<unknown>; started: number; by: typeof fetch }>();

/** How long a read on its way is joined rather than repeated: a first draw's burst is within a
 *  frame or two, and a read that has hung for longer must not hold up a later ask of the same. */
const JOIN_MS = 250;

export async function request<T>(
	method: string,
	path: ApiPath,
	options: RequestOptions = {}
): Promise<T> {
	inFlight += 1;
	try {
		if (method !== 'GET' || options.signal || options.keepalive || options.asBlob) {
			return await send<T>(method, path, options);
		}
		const address = addressOf(path, options.query).toString();
		const going = reading.get(address);
		/* A copy for whoever joined, so no caller can change what another holds. */
		/* Joined only while the same `fetch` is in place: a test that stands in another one, or
		   a page that was given a new one, must not share what the old one has on its way. */
		if (
			going !== undefined &&
			going.by === globalThis.fetch &&
			performance.now() - going.started < JOIN_MS
		) {
			return structuredClone(await going.read) as T;
		}
		const read = send<T>(method, path, options);
		const entry = { read, started: performance.now(), by: globalThis.fetch };
		reading.set(address, entry);
		try {
			return await read;
		} finally {
			if (reading.get(address) === entry) reading.delete(address);
		}
	} finally {
		inFlight -= 1;
	}
}

function addressOf(path: ApiPath, query: RequestOptions['query']): URL {
	const url = new URL(API_PREFIX + path, window.location.origin);
	for (const [key, value] of Object.entries(query ?? {})) {
		if (value === undefined) continue;
		// `append` per value for an array, never `set`: `set` would replace the previous one, so a
		// list of three would arrive as its last member and read as a much wider question.
		if (Array.isArray(value)) for (const one of value) url.searchParams.append(key, String(one));
		else url.searchParams.set(key, String(value));
	}
	return url;
}

async function send<T>(method: string, path: ApiPath, options: RequestOptions): Promise<T> {
	const url = addressOf(path, options.query);

	// A file upload is multipart, and the browser has to write the content-type itself: it carries
	// a boundary marker only it knows, so setting the header by hand produces a body the server
	// cannot parse.
	const isForm = options.body instanceof FormData;

	const headers: Record<string, string> = {};
	if (options.body !== undefined && !isForm) headers['content-type'] = 'application/json';
	// Which kind of window asked, on every request, for the records the server stamps with it.
	headers[CLIENT_HEADER] = clientKind();

	// Only on the requests that change something. A GET carrying it would put the token in more
	// places for no gain: there is nothing for it to protect there.
	if (MUTATING.has(method.toUpperCase()) && csrfToken) {
		headers['x-csrf-token'] = csrfToken;
	}

	const body =
		options.body === undefined
			? undefined
			: isForm
				? (options.body as FormData)
				: JSON.stringify(options.body);

	let response: Response;
	try {
		response = await fetch(url, {
			method,
			headers,
			// The session is an http-only cookie: the app cannot read it, and does not need to.
			credentials: 'same-origin',
			body,
			signal: options.signal,
			keepalive: options.keepalive
		});
	} catch (error) {
		// Aborted by the caller is the caller's to handle; anything else is a server that did not
		// answer.
		if (options.signal?.aborted || (error instanceof Error && error.name === 'AbortError')) {
			throw error;
		}
		throw new Unreachable();
	}

	if (!response.ok) {
		if (endsTheSession(response)) onSessionEnded();
		if (locksTheSession(response)) onSessionLocked();
		throw new ApiError(
			response.status,
			messageFor(response.status),
			await detailOf(response),
			response.headers?.get(FIELD_HEADER) ?? undefined
		);
	}

	if (response.status === 204) return undefined as T;
	if (options.asBlob) return (await response.blob()) as T;
	return (await response.json()) as T;
}

/* The session has ended underneath somebody who is still looking at the app. */
function endsTheSession(response: Response): boolean {
	return response.status === 401 && response.headers.get('www-authenticate') === 'Session';
}

/* Sift is locked on this session. A different answer from a 401 and it has to stay different. */
const LOCKED = 423;

function locksTheSession(response: Response): boolean {
	return response.status === LOCKED && response.headers.get('sift-locked') === 'session';
}

/* Screens a session refusal must NEVER navigate away from. */
const NO_SESSION_TO_END_HERE = ['/connect', '/setup', '/login'];

function noSessionToEndHere(): boolean {
	if (typeof window === 'undefined') return false;
	return NO_SESSION_TO_END_HERE.some((path) => window.location.pathname.startsWith(path));
}

function onSessionLocked(): void {
	if (typeof window === 'undefined') return;
	if (noSessionToEndHere()) return;
	if (window.location.pathname.startsWith('/locked')) return;
	// A full load, for the reason the sign-out below does one: everything the page is holding was
	// fetched while the session was open, and the point of locking is that it stops being on screen.
	window.location.replace('/locked');
}

function onSessionEnded(): void {
	if (typeof window === 'undefined') return;
	/* `/login` is not checked again below: two guards where either one alone holds is a rule no
	 * test can fail on, so breaking one of them would go unnoticed. */
	if (noSessionToEndHere()) return;
	// A full load rather than a client-side navigation, deliberately.
	window.location.replace('/login');
}

export const api = {
	get: <T>(path: ApiPath, options?: RequestOptions) => request<T>('GET', path, options),
	post: <T>(path: ApiPath, options?: RequestOptions) => request<T>('POST', path, options),
	put: <T>(path: ApiPath, options?: RequestOptions) => request<T>('PUT', path, options),
	patch: <T>(path: ApiPath, options?: RequestOptions) => request<T>('PATCH', path, options),
	del: <T>(path: ApiPath, options?: RequestOptions) => request<T>('DELETE', path, options),
	/* A POST whose answer is a file rather than JSON. */
	postForFile: (path: ApiPath, body: unknown) => request<Blob>('POST', path, { body, asBlob: true })
};
