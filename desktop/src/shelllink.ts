/* The door the backend on THIS machine asks its own shell through.
 *
 * WHY IT EXISTS. A window onto a library on another computer (client mode) draws that server's
 * settings, and every admin press there has to act on the SERVER machine, never on the computer the
 * window happens to be on. Starting with Windows and the firewall rule are facts only the shell of
 * the server machine can read or change: the backend is a Python process that cannot register a
 * login item or raise Windows' administrator prompt. So the server's shell offers the backend it
 * started a small set of acts, and the backend answers its own admin routes by asking it. The
 * client's shell never acts; its page asks the server's API like any other.
 *
 * WHAT KEEPS IT NARROW. It listens on 127.0.0.1 only, on a port the operating system picks, and
 * every request carries a secret made fresh for this launch and handed to the backend in its
 * environment, which only the process that spawned it can set. The acts are named and take one
 * word each (a boolean, a scope, a count); nothing here takes a command or a program. The same
 * rules the page's own verbs keep (`verbs.ts`) hold here: a firewall scope that is not `any` is the
 * narrow one, and a switch that is not `true` is off.
 *
 * TWO ACTS NAME A FOLDER, and each is held to what the page's own verb allows. Opening a library
 * takes a data folder and refuses any this copy has not opened before, as `openLibrary` does for
 * the page. Moving the storage folders takes the empty folder to move into, chosen in the server's
 * own folder browser, and every refusal `storage.refuse` makes is made before the backend stops.
 * Installing an update takes NOTHING: the shell reads the feed, checks the signature and refuses
 * anything not newer than itself, exactly as the page's verb does.
 *
 * AN ACT THAT STOPS THE BACKEND IS ANSWERED FIRST. Sharing, a move, a library and an update each
 * stop the process whose request is waiting on the answer, so the answer leaves first and the act
 * runs after it (`Deferred`). The page asking waits for a new run of the server to answer.
 *
 * THE ACTS THAT MUST BE PRESSED HERE. Opening the firewall raises Windows' own administrator
 * prompt, and an update opens the installer, both on THIS machine's screen. Nothing on the other
 * computer can answer them, which is the property rather than a gap: the screen there says to
 * approve them at the computer running Sift. So does choosing a database file in the picker,
 * which is never offered over the link at all.
 */

import { randomBytes, timingSafeEqual } from 'node:crypto';
import * as http from 'node:http';
import type { AddressInfo } from 'node:net';

import type { LibraryList, Settled } from '../../shared/bridge';
import type { FirewallReport, FirewallScope } from './firewall';
import { linesAsked, log } from './log';
import type { StartWithWindows } from './startup';
import type { StorageReport } from './storage';
import type { UpdateOutcome } from './update';
import type { ShellLog } from './verbs';

/** Whether the library is offered to the network, as this shell knows it. */
export interface LinkSharing {
	enabled: boolean;
	live: boolean;
	address: string | null;
	port: number;
}

/** What the backend may ask this shell to read or do. Handed in by `main`, which owns each one. */
export interface ShellActs {
	/** What this computer is called on its network, for "Sift is restarting on <name>". */
	machine(): string | null;
	/** The login item. Null where this copy cannot register one (a checkout). */
	startup: StartWithWindows | null;
	/** Whether the library is offered to the network. */
	sharing(): LinkSharing;
	/** Whether Windows lets other computers reach the port. A plain read, no rights needed. */
	firewall(): Promise<FirewallReport>;
	/** Windows' own prompt is raised on this screen, and what is true afterwards is answered. */
	openFirewall(scope: FirewallScope): Promise<FirewallReport>;
	/** Offer the library to the network or stop. Restarts the backend, so it is answered first. */
	setSharing(on: boolean): Promise<Deferred<Settled>>;
	/** Where the two storage folders are, and what the last move asked over this link came to. */
	storage(): Promise<LinkStorage>;
	/** Move both folders into `folder`: refused in words before anything stops, or answered first. */
	moveStorage(folder: string): Promise<Deferred<Settled>>;
	/** Read the feed, check the signature, and open the installer of a NEWER release on this screen. */
	update(): Promise<Deferred<UpdateOutcome>>;
	/** The end of this shell's own log. */
	log(lines: number): ShellLog;
	/** Every library this copy has opened, and which one is open. */
	libraries(): LibraryList;
	/** Open a library this copy has opened before: refused in words, or answered first. */
	openLibrary(dataDir: string): Promise<Deferred<Settled>>;
}

/**
 * An act that stops the backend asking for it. `answer` is sent at once; `after`, when there is
 * one, runs only once that answer has left, because the request waiting on it belongs to the
 * process `after` stops. No `after` is an act refused, or one with nothing to do.
 */
export interface Deferred<T> {
	answer: T;
	after?: () => Promise<void>;
}

/** The storage folders, and what the last move asked over this link came to (null: none asked). */
export interface LinkStorage extends StorageReport {
	lastMove: Settled | null;
}

/** Where the backend reaches the link, handed to it in its environment. */
export interface ShellLink {
	url: string;
	token: string;
	close(): Promise<void>;
}

/** The facts the backend reads in one ask. */
export interface ShellFacts {
	machine: string | null;
	startsWithWindows: boolean | null;
	sharing: LinkSharing;
}

/** A body larger than this is refused unread: every act takes one word. */
const MAX_BODY = 1024;

/** Whether the secret the request carries is this launch's. Constant time, so a guess learns nothing. */
export function carriesToken(header: string | undefined, token: string): boolean {
	if (typeof header !== 'string' || !header.startsWith('Bearer ')) return false;
	const given = Buffer.from(header.slice('Bearer '.length));
	const wanted = Buffer.from(token);
	return given.length === wanted.length && timingSafeEqual(given, wanted);
}

function answer(response: http.ServerResponse, status: number, body: unknown): void {
	const text = JSON.stringify(body);
	response.writeHead(status, {
		'content-type': 'application/json',
		'content-length': Buffer.byteLength(text)
	});
	response.end(text);
}

/** What a body too large to be one word reads as: refused, and never parsed. */
const TOO_BIG = Symbol('too-big');

function readBody(request: http.IncomingMessage): Promise<unknown> {
	return new Promise((resolve) => {
		let size = 0;
		const parts: Buffer[] = [];
		request.on('data', (chunk: Buffer) => {
			size += chunk.length;
			if (size > MAX_BODY) {
				resolve(TOO_BIG);
				return;
			}
			parts.push(chunk);
		});
		request.on('end', () => {
			if (size > MAX_BODY) return;
			try {
				resolve(JSON.parse(Buffer.concat(parts).toString('utf8')) as unknown);
			} catch {
				resolve(undefined);
			}
		});
	});
}

/** The field `name` of a parsed body, or undefined when the body is not an object. */
function field(body: unknown, name: string): unknown {
	return typeof body === 'object' && body !== null
		? (body as Record<string, unknown>)[name]
		: undefined;
}

/** What one ask comes to: the answer, and what runs once it has left. */
export interface Done {
	status: number;
	body: unknown;
	after?: () => Promise<void>;
}

function deferred<T>(taken: Deferred<T>): Done {
	const done: Done = { status: 200, body: taken.answer };
	if (taken.after !== undefined) done.after = taken.after;
	return done;
}

/** A string that is not empty, or null: the one shape a folder named over the link can have. */
function named(value: unknown): string | null {
	return typeof value === 'string' && value !== '' ? value : null;
}

/**
 * Everything the backend may ask, by method and path. Exported for the tests, which call it bare.
 * A GET's `body` is its query, read as an object.
 */
export async function act(
	acts: ShellActs,
	method: string,
	path: string,
	body: unknown
): Promise<Done> {
	if (method === 'GET' && path === '/facts') {
		const facts: ShellFacts = {
			machine: acts.machine(),
			startsWithWindows: acts.startup?.read() ?? null,
			sharing: acts.sharing()
		};
		return { status: 200, body: facts };
	}
	if (method === 'PUT' && path === '/start-with-windows') {
		/* Checked against `true` rather than cast, as the page's own verb is: it adds a program to
		   what this machine runs at every sign-in. */
		const on = field(body, 'on') === true;
		const now = acts.startup?.write(on) ?? null;
		log.info('shell_link.start_with_windows', { on, now });
		return { status: 200, body: { startsWithWindows: now } };
	}
	if (method === 'GET' && path === '/firewall') {
		return { status: 200, body: await acts.firewall() };
	}
	if (method === 'POST' && path === '/firewall') {
		const scope: FirewallScope = field(body, 'scope') === 'any' ? 'any' : 'private';
		log.info('shell_link.open_firewall', { scope });
		return { status: 200, body: await acts.openFirewall(scope) };
	}
	if (method === 'PUT' && path === '/sharing') {
		/* Against `true`, as the page's own switch is: on opens the library to the network. */
		const on = field(body, 'on') === true;
		log.info('shell_link.sharing', { on });
		return deferred(await acts.setSharing(on));
	}
	if (method === 'GET' && path === '/storage') {
		return { status: 200, body: await acts.storage() };
	}
	if (method === 'POST' && path === '/storage/move') {
		const folder = named(field(body, 'folder'));
		if (folder === null) return { status: 400, body: { detail: 'Name one folder.' } };
		log.info('shell_link.move_storage', {});
		return deferred(await acts.moveStorage(folder));
	}
	if (method === 'POST' && path === '/update') {
		/* Whatever the body says is not read: the feed, the key and the version this copy is are
		   the shell's own, so no ask can name what gets installed. */
		log.info('shell_link.update', {});
		return deferred(await acts.update());
	}
	if (method === 'GET' && path === '/log') {
		return { status: 200, body: acts.log(linesAsked(field(body, 'lines'))) };
	}
	if (method === 'GET' && path === '/libraries') {
		return { status: 200, body: acts.libraries() };
	}
	if (method === 'POST' && path === '/libraries/open') {
		const dataDir = named(field(body, 'dataDir'));
		if (dataDir === null) return { status: 400, body: { detail: 'Name one library.' } };
		log.info('shell_link.open_library', {});
		return deferred(await acts.openLibrary(dataDir));
	}
	return { status: 404, body: { detail: 'No such act.' } };
}

/**
 * Open the link for this launch. One per shell, before the backend starts, so every start of the
 * backend (a restart, a library switch) is handed the same address and secret.
 */
export function openShellLink(
	acts: ShellActs,
	token: string = randomBytes(32).toString('hex')
): Promise<ShellLink> {
	const server = http.createServer((request, response) => {
		void (async () => {
			if (!carriesToken(request.headers.authorization, token)) {
				answer(response, 401, { detail: 'Not this launch.' });
				return;
			}
			const [path = '', query = ''] = (request.url ?? '').split('?');
			const body =
				request.method === 'GET'
					? Object.fromEntries(new URLSearchParams(query))
					: await readBody(request);
			if (body === TOO_BIG) {
				answer(response, 413, { detail: 'Every act takes one word.' });
				return;
			}
			try {
				const done = await act(acts, request.method ?? '', path, body);
				const after = done.after;
				if (after !== undefined) {
					/* Once the answer has left, and not before: the request waiting on it belongs
					   to the backend this is about to stop. */
					response.once('finish', () => {
						void after().catch((error: unknown) => {
							log.warning('shell_link.after_failed', {
								path,
								reason: error instanceof Error ? error.message : String(error)
							});
						});
					});
				}
				answer(response, done.status, done.body);
			} catch (error) {
				log.warning('shell_link.failed', {
					path,
					reason: error instanceof Error ? error.message : String(error)
				});
				answer(response, 500, { detail: 'The act did not finish.' });
			}
		})();
	});
	return new Promise((resolve, reject) => {
		server.once('error', reject);
		/* Port 0: the operating system picks a free one, so there is no second fixed number to
		   collide with another program, and nothing outside this machine can reach it. */
		server.listen(0, '127.0.0.1', () => {
			const { port } = server.address() as AddressInfo;
			resolve({
				url: `http://127.0.0.1:${port}`,
				token,
				close: () => new Promise((done) => server.close(() => done()))
			});
		});
	});
}
