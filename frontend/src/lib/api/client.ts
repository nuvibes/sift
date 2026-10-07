import type { paths } from './schema';
import { CLIENT_HEADER, clientKind } from '$lib/shell/client-kind';
import { UNREACHABLE } from '$lib/shell/unreachable';

/* The one way the app talks to the server.
 *
 * Everything goes through here so that three things are decided once instead of in every screen:
 * the session travels with the request, a state-changing request carries its CSRF token, and a
 * failure comes back as one shape with a sentence a person can read.
 */

export const API_PREFIX = '/api';

const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

/**
 * Server paths, from the generated schema, so a typo is a build error, not a 404 at runtime.
 *
 * The schema names every address with its `/api` prefix and with its parameters in braces
 * (`/api/assets/{asset_id}/vault`); a caller writes the address without the prefix and with the id
 * already in it. So the two are joined here: the prefix comes off, and each `{...}` becomes an
 * opening for whatever a caller interpolates.
 *
 * What that catches is a wrong or renamed address, which is the whole of what it can catch: the
 * openings accept any text, so it does not check that an id is an id. That is the right trade. The
 * failure this exists for is an endpoint being renamed on the server and a screen still asking for
 * the old one, which reaches somebody as an empty screen with no error, and now cannot be built.
 */
type WithoutPrefix<Path> = Path extends `${typeof API_PREFIX}${infer Rest}` ? Rest : never;

/** `/folders/{folder_id}/vault` -> `/folders/${string}/vault`. */
type WithValues<Path extends string> = Path extends `${infer Head}{${string}}${infer Tail}`
	? `${Head}${string}${WithValues<Tail>}`
	: Path;

export type ApiPath = WithValues<WithoutPrefix<keyof paths>>;

export class ApiError extends Error {
	readonly status: number;
	/**
	 * What the server said, when it said anything, and nothing is shown from it unless a screen asks.
	 *
	 * `message` stays the flat one-liner below, and that is the point of them being two things. Most
	 * of what a server says about a refusal is either meaningless to the person reading it or is
	 * exactly what should not be said: a 404 that explains itself is a 404 that has just confirmed
	 * the thing exists. So the default is the one-liner, always, and nothing has to remember to make
	 * it so.
	 *
	 * Some refusals are the product, though. "Sift is already watching that folder as part of
	 * Videos" is a sentence somebody can act on, written for them, and replacing it with "That
	 * request was not valid" leaves them stuck with no idea what to change. A screen that knows the
	 * endpoint it called writes for people can reach for this. It is opt-in for each screen, one
	 * screen at a time, and never the default.
	 */
	readonly detail?: string;

	/**
	 * The field a refusal is about, where the route names one (the `Sift-Field` header): so a form
	 * can say the refusal beside that field rather than at its foot, far from what to change.
	 */
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

/**
 * The server did not answer at all: nothing came back to read a status from.
 *
 * Its message IS the one sentence for that (`UNREACHABLE`), set here, where the request failed,
 * rather than left to each screen. The browser's own rejection says "Failed to fetch", and a screen
 * that shows a thrown message as it stands would draw exactly that when Sift stopped. A request the
 * caller aborted is not this: its `AbortError` reaches the caller untouched.
 */
export class Unreachable extends Error {
	constructor() {
		super(UNREACHABLE);
		this.name = 'Unreachable';
	}
}

/**
 * Whether a failure actually means "there is no such thing", rather than "that did not work".
 *
 * The distinction is the whole point. A screen that catches everything and says the thing is GONE
 * is making a statement about somebody's library out of what was really a fault in Sift. And it
 * is the most alarming statement a screen can make, because the reader has no way to tell it from
 * the truth: every group on the faces screen saying "that group is not there any more" when the
 * request was refused as malformed.
 *
 * A 404 is the one status that is a fact about the library, and it is deliberately the same answer
 * for "no such id" and "not yours". See `messageFor`. Everything else is Sift's problem to own
 * and say so.
 */
export function isMissing(error: unknown): boolean {
	return error instanceof ApiError && error.status === 404;
}

/* The CSRF token, held in memory only.
 *
 * It is derived from the session cookie, so it is not a second secret to look after: the server
 * recomputes it per request and compares. Keeping it in a variable rather than in storage means a
 * tab that reloads simply asks for it again (see `signedIn`), and nothing is left behind on a
 * shared machine.
 */
let csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
	csrfToken = token;
}

/* What a failed request says.
 *
 * 404 is the interesting one, and it is the only one worth being careful about. The server answers
 * an identical 404 whether a thing does not exist or exists and is not yours, so that asking for
 * an id is not a way to find out whether it is real. A friendlier "you do not have access to this"
 * would undo that from the client: the message itself would be the answer the 404 refuses to give.
 * It says "Not found", always, and there is a test that keeps it that way.
 */
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

/**
 * The server's own words for a refusal, if it sent any and they are words.
 *
 * Nothing here is trusted to be present or to be a string: a refusal can carry a validation report,
 * an empty body, or HTML from something in front of the app that is not the app. Anything that is
 * not plain prose comes back undefined, and the caller falls back to the one-liner.
 */
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
	/*
	 * One value per name, or SEVERAL under one name.
	 *
	 * An array is written as a repeated parameter (`?hair_color=BLONDE&hair_color=RED`), which
	 * is what every list route in this application reads as a set of values for one filter. With
	 * `set` and a single value only, a caller holding two would have to pick one or join them with
	 * a separator this file knows nothing about; the entity walls' filtering is repeated keys, so
	 * that is the ordinary case rather than a corner of it.
	 */
	query?: Record<string, string | number | boolean | readonly string[] | undefined>;
	signal?: AbortSignal;
	// Let the request finish even if the page is going away. Used for the last post on the way out
	// of the player, which fires while the tab is closing and would otherwise be cancelled.
	keepalive?: boolean;
	/* Take the answer as bytes rather than parsing it. For the one endpoint that returns a file:
	   `JSON.parse` on a zip is a confusing error a long way from its cause. */
	asBlob?: boolean;
}

/*
 * How many requests are on their way right now.
 *
 * One question with one reader today: whether a screen that is filling itself from the server has
 * FINISHED. See `$lib/settings-ui/settings-anchor`, which waits for a settings pane to finish drawing before
 * it concludes that a row is not coming. A screen's own "still loading" drawing is not a signal it
 * can rely on: most panes draw nothing at all while their request is out. Counted here because
 * every request goes through here, so the answer cannot miss one.
 */
let inFlight = 0;

/** Whether any request is still on its way. */
export function requestsInFlight(): boolean {
	return inFlight > 0;
}

/*
 * Reads on their way, by address. A page's first draw asks for the same few addresses from several
 * places at the same time (who is signed in, the settings, the folders); a second ask of an address
 * already on its way joins it instead of going again. Only a plain read joins: one with its own
 * abort signal, or kept alive past the page, or taken as bytes, goes on its own.
 */
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
		/* Joined only while the same `fetch` is in place: a test that stands in another one, or a
		   page that was given a new one, must not share what the old one has on its way. */
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

	// A file upload is multipart, and the browser has to write the content-type itself: it carries a
	// boundary marker only it knows, so setting the header by hand produces a body the server cannot
	// parse. Everything else is JSON. This is the one place that choice is made.
	const isForm = options.body instanceof FormData;

	const headers: Record<string, string> = {};
	if (options.body !== undefined && !isForm) headers['content-type'] = 'application/json';
	// Which kind of window asked, on every request, for the records the server stamps with it. See
	// `client-kind.ts`.
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
			// The session is an http-only cookie: the app cannot read it, and does not need to. This is
			// what sends it. `same-origin` and not `include`, because the API is the same origin: if
			// that ever stops being true, this should break rather than start posting the session
			// somewhere new.
			credentials: 'same-origin',
			body,
			signal: options.signal,
			keepalive: options.keepalive
		});
	} catch (error) {
		// Aborted by the caller is the caller's to handle; anything else is a server that did not
		// answer. See `Unreachable`.
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

/*
 * The session has ended underneath somebody who is still looking at the app.
 *
 * Three ways that happens and they are all real: an admin blocks the account, an admin resets its
 * password, or the session simply expired. In every one of them the pages already on screen keep
 * working (they were drawn from data that arrived while the session was alive), and the person
 * sees nothing but "Please sign in" on whatever they touch next. That reads as the app being
 * broken, not as having been signed out, and there is nothing on screen that takes them anywhere.
 *
 * So a 401 ends the session here, once, and sends them to the sign-in screen.
 *
 * Not every 401 means that. Signing in with a wrong password is a 401. So is mistyping your current
 * password on the change-password form, and so is a wrong PIN. Bouncing on those would sign
 * somebody out for a typo: worse than the problem this solves.
 *
 * The two are told apart by the server rather than by a list of addresses here. Only the missing-
 * session refusal carries `WWW-Authenticate: Session`; a wrong credential does not. A list of paths
 * would have every route that checks a credential remembered in two places, and the one that gets
 * forgotten fails in the direction that logs somebody out.
 */
function endsTheSession(response: Response): boolean {
	return response.status === 401 && response.headers.get('www-authenticate') === 'Session';
}

/* Sift is locked on this session.
 *
 * A different answer from a 401 and it has to stay different. A 401 means "sign in", and treating
 * this as one would throw away the session the PIN was about to open, turning a five-second
 * unlock into a password. 423 means the session is real and is shut, which is a screen with a PIN
 * box on it.
 *
 * The server decides this, not the client: the mark is on the session row, so the same answer comes
 * back to every tab, every reload and anything else holding the credential. There is nothing drawn
 * over anything here: by the time this runs the request has already been refused.
 *
 * ## TWO THINGS ANSWER 423 AND ONLY ONE OF THEM IS THIS ONE
 *
 * The other is `kernel.reach.vault_locked`: ONE named file the asker's own vault is concealing,
 * raised by every route that writes to a file: a heart, a star, a tag, a collection. That is an
 * ordinary refusal of an ordinary write. The screen behind it is fine, the rest of the library is
 * fine, and the answer is a message with an Unlock button on it.
 *
 * The status alone cannot tell them apart: read as the session lock, every one of those refusals
 * would load the lock screen and ask for a PIN, and favouriting a hidden file would sign you out.
 *
 * `locksTheSession` is the discriminator and it reads a header, exactly as `endsTheSession` above
 * does and for the same reason. The mark is on the SESSION refusal, so a 423 carrying no mark is
 * left to the caller to report, which is the harmless direction, and the direction the 401 pair
 * chose too.
 */
const LOCKED = 423;

function locksTheSession(response: Response): boolean {
	return response.status === LOCKED && response.headers.get('sift-locked') === 'session';
}

/* Screens a session refusal must NEVER navigate away from.
 *
 * One rule with two cases behind it: on all of these, being signed out is the NORMAL state. A 401
 * is the screen doing its job, not a session that has ended, so bouncing to a sign-in form is
 * answering a question nobody asked.
 *
 * `/connect` is the desktop client asking which computer the library is on. Nothing there is
 * signed in and nothing there could be, so a 401 from any stray request means "the server you
 * named wants a sign-in", which is the next screen rather than a reason to leave this one.
 * Without this, one request nobody thought about sends somebody from the screen that asks for an
 * address to a sign-in form for a server they have not chosen yet, and there is no way back.
 *
 * `/setup` and `/login` create and open the only account there is, and without them here it is a
 * real loop, not a precaution. The root layout re-reads the session whenever the WINDOW takes
 * focus, and in the desktop app, clicking into a field does that. Signed out, `/auth/me`
 * answers 401 carrying the session challenge, so every click on the account form would do a full
 * `location.replace('/login')`; `/login` would send it straight back to `/setup`, because no
 * admin exists yet; and the form would come back empty.
 *
 * The list is here rather than at the call sites for the reason `endsTheSession` gives above: a
 * rule remembered in two places is a rule that gets forgotten in one of them, and the one that is
 * forgotten fails in the direction that throws work away.
 */
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
	 * test can fail on, so breaking one of them would go unnoticed. The list is the rule.
	 */
	if (noSessionToEndHere()) return;
	// A full load rather than a client-side navigation, deliberately. Everything held in memory
	// (who was signed in, the vault, a grid of things this account can no longer see) goes with the
	// page, and starting the next session from a blank slate is worth more here than a smooth
	// transition. `replace` so the back button does not walk into the signed-out app.
	window.location.replace('/login');
}

export const api = {
	get: <T>(path: ApiPath, options?: RequestOptions) => request<T>('GET', path, options),
	post: <T>(path: ApiPath, options?: RequestOptions) => request<T>('POST', path, options),
	put: <T>(path: ApiPath, options?: RequestOptions) => request<T>('PUT', path, options),
	patch: <T>(path: ApiPath, options?: RequestOptions) => request<T>('PATCH', path, options),
	del: <T>(path: ApiPath, options?: RequestOptions) => request<T>('DELETE', path, options),
	/* A POST whose answer is a file rather than JSON.
	 *
	 * Through the same request path as everything else, so it carries the same credential and the
	 * same CSRF token and reports a refusal the same way. Only the last step differs: the body is
	 * taken as bytes instead of parsed, because a pack is a zip and JSON.parse on a zip is a
	 * confusing error a long way from the cause.
	 */
	postForFile: (path: ApiPath, body: unknown) => request<Blob>('POST', path, { body, asBlob: true })
};
