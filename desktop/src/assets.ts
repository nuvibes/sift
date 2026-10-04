/* Getting at an asset's actual file, on behalf of the window.
 *
 * A real Windows drag hands the receiving application a PATH ON THE LOCAL DISK: the format
 * Windows uses (CF_HDROP) carries nothing else. So before anything can be dragged out, the shell
 * has to have a real file. In standalone that file is already there and is dragged where it lies;
 * in client mode the bytes are on somebody else's machine and a copy has to be fetched first.
 *
 * THE PAGE NEVER NAMES A FILE. It passes an asset id and nothing more, and this module works out
 * the rest. That constraint is the whole security design of the bridge: a page that could name a
 * path could name any path on the machine, and anything that got code into that page (a bad
 * dependency, a link on a saved origin) could then drag somebody's documents into a chat window.
 *
 * THE FETCH ITSELF HAPPENS IN ANOTHER PROCESS, and that is not an optimisation. See transfer.ts:
 * the main process's event loop is blocked while a drop is being taken, so a transfer running on it
 * could never produce the bytes the receiver is waiting for.
 */

import { app, session } from 'electron';
import * as fs from 'node:fs';
import * as path from 'node:path';

import type { Arriving, Progress } from './transfer';
import { transfers } from './transfers';

export type { Arriving, Progress };

/** What the backend says about where one asset's file is. */
export interface LocalFile {
	/** The absolute path, or null when Sift is running on another machine. */
	path: string | null;
	/** The same file named so it means the same thing on any computer, or null. See `reachedHere`. */
	shared_path?: string | null;
	filename: string;
	size_bytes: number | null;
	/** The file says where it was made, so it never leaves as it is: the server answers no path
	 *  and no share path for it, and the copy is fetched from `/outgoing`, which takes the place
	 *  out, never from `/stream`. Absent from a server older than the answer. */
	holds_a_place?: boolean;
}

/* How long a share is given to answer before the drag stops waiting on it.
 *
 * A share whose server is asleep does not refuse quickly: the operating system waits out its own
 * timeout, which is tens of seconds. That wait would sit between somebody pressing the mouse and
 * anything happening, so it is cut short and the drag falls back to fetching a copy. */
const SHARE_TIMEOUT_MS = 1500;

/**
 * The share path, if THIS machine can reach it and it is the file the server meant. Else null.
 *
 * WHY THIS IS THE WHOLE ANSWER FOR A LIBRARY ON A NAS. Both machines can see the share, so the
 * second one has no reason to copy a video across the network in order to hand it to something
 * running beside it. It hands over the path, and the receiving application reads it exactly as it
 * would on the machine Sift runs on. No transfer, no stream, no waiting: the same drag as at home.
 *
 * TWO THINGS ARE CHECKED AND BOTH MATTER. That it is there at all, because a share can be
 * unreachable from this machine even when the server can see it. And that its SIZE is the size the
 * server described, because a share can be mounted from a different server under the same name, and
 * the failure that would produce is the worst one this feature has: a confident drag of the wrong
 * video into somebody's chat window. A mismatch falls back to fetching a copy, which is slower and
 * certainly right.
 */
export async function reachedHere(file: LocalFile): Promise<string | null> {
	const shared = file.shared_path;
	if (typeof shared !== 'string' || shared === '') return null;
	const asked = fs.promises.stat(shared).catch(() => null);
	const timeout = new Promise<null>((settle) => setTimeout(() => settle(null), SHARE_TIMEOUT_MS));
	const found = await Promise.race([asked, timeout]);
	if (found === null || !found.isFile()) return null;
	if (file.size_bytes !== null && found.size !== file.size_bytes) return null;
	return shared;
}

/* Everything fetched for a drag lands here, under the operating system's own temp folder.
 *
 * One directory rather than scattered files, because that is what makes the cleanup policy a
 * single line: the whole folder goes at startup. Startup and not shutdown, deliberately: a
 * shutdown-only sweep never runs after a crash or a force-quit, which are exactly the times
 * something is left behind. */
const TEMP_DIRECTORY = 'sift-drag';

function tempRoot(): string {
	return path.join(app.getPath('temp'), TEMP_DIRECTORY);
}

/** Throw away everything a previous run fetched. Called once, before any window exists. */
export function clearFetchedFiles(): void {
	/* Failure is ignored on purpose: a file another process still holds open is not a reason to
	 * refuse to start, and the next launch will get it. */
	fs.rmSync(tempRoot(), { recursive: true, force: true, maxRetries: 2 });
}

/* A name that is only ever a name.
 *
 * It arrives from the server, which in client mode is another machine, so it is untrusted input
 * that this module is about to use as a filename. `..\\..\\Startup\\evil.lnk` would otherwise
 * write outside the temp folder entirely. Sanitised HERE rather than at the server, because this
 * is the side that does the writing and the rule belongs where the risk is. */
const UNSAFE_IN_NAME = /[^A-Za-z0-9._ -]+/g;

export function safeName(name: string): string {
	const cleaned = name.replace(UNSAFE_IN_NAME, '_').replace(/^\.+/, '').trim().slice(0, 120);
	return cleaned === '' ? 'file' : cleaned;
}

/** Ask where an asset's file is. Null when the asset is gone, hidden, or nobody is signed in. */
export async function localFile(origin: string, assetId: string): Promise<LocalFile | null> {
	const url = `${origin}/api/assets/${encodeURIComponent(assetId)}/local-file`;
	try {
		const response = await session.defaultSession.fetch(url);
		if (!response.ok) return null;
		const answer: unknown = await response.json();
		return isLocalFile(answer) ? answer : null;
	} catch {
		/* No network, a server that has gone away, a reply that is not JSON. All the same answer:
		 * there is no file to drag, and the caller shows nothing rather than an exception. */
		return null;
	}
}

/* Checked, not cast. The answer decides what path is handed to the native drag and what name a
 * file is written under, and in client mode it comes from another computer. An answer of the
 * wrong shape (a server a version apart, a proxy's error page that happens to be JSON) would
 * otherwise reach the drag as a path of `undefined`, because `undefined !== null` reads as "a
 * local file".
 */
function isLocalFile(v: unknown): v is LocalFile {
	if (typeof v !== 'object' || v === null) return false;
	const o = v as Record<string, unknown>;
	const shared = o.shared_path;
	return (
		(o.path === null || typeof o.path === 'string') &&
		(shared === undefined || shared === null || typeof shared === 'string') &&
		(o.holds_a_place === undefined || typeof o.holds_a_place === 'boolean') &&
		typeof o.filename === 'string' &&
		(o.size_bytes === null || typeof o.size_bytes === 'number')
	);
}

/**
 * The two names a fetch of this asset uses. Computed without touching the network or the disk.
 *
 * Separate from the fetch itself because the DRAG has to be told them BEFORE the fetch has got
 * anywhere: the whole point of a streamed drag is that the gesture and the transfer happen at the
 * same time, so the names cannot be something the transfer hands back when it finishes.
 */
export function arrivingAt(assetId: string, file: LocalFile): Arriving {
	/* A copy without a place is kept apart from one of the original, so a copy fetched before the
	   file held a place can never be handed to a drag that must carry none. */
	const directory = path.join(
		tempRoot(),
		file.holds_a_place === true ? `${safeName(assetId)}-unplaced` : safeName(assetId)
	);
	const finished = path.join(directory, safeName(file.filename));
	return { partial: `${finished}.partial`, finished };
}

/** Whether the whole file is already here from an earlier drag. */
export function alreadyFetched(assetId: string, file: LocalFile): string | null {
	const { finished } = arrivingAt(assetId, file);
	return fs.existsSync(finished) ? finished : null;
}

/* What the window is signed in with, as a header a plain HTTP client can send.
 *
 * READ HERE because this is the only side that can read it: it belongs to the window's session,
 * which exists in the main process and nowhere else. It is then handed to the process that does the
 * fetching (see the note at the top of transfer.ts for why that boundary is one inside a single
 * application rather than one between parties).
 *
 * Scoped to the ORIGIN being fetched from, so a machine with several servers saved never hands one
 * of them what belongs to another.
 */
async function signedInHeaders(origin: string): Promise<Record<string, string>> {
	try {
		const held = await session.defaultSession.cookies.get({ url: origin });
		if (held.length === 0) return {};
		return { Cookie: held.map((one) => `${one.name}=${one.value}`).join('; ') };
	} catch {
		/* Nothing to send is not an error to raise here. The request goes out without it and the
		 * server answers as it would any anonymous caller, which for a private library is a refusal,
		 * and a refusal is reported as a transfer that produced nothing. */
		return {};
	}
}

/**
 * Where a drag's bytes come from. A file that says where it was made comes through the server's
 * door (`/outgoing`), which hands out a copy with the place taken out; any other file is the
 * stream itself, exactly the bytes on the disk.
 */
export function outgoingUrl(origin: string, assetId: string, file: LocalFile): string {
	const id = encodeURIComponent(assetId);
	return file.holds_a_place === true
		? `${origin}/api/assets/${id}/outgoing`
		: `${origin}/api/assets/${id}/stream`;
}

/**
 * Fetch an asset's bytes to a local file, so there is something to drag.
 *
 * NO SIZE CAP, deliberately: a clip is whatever size it is, and refusing the large ones would make
 * the feature unavailable exactly where dragging by hand is most tedious.
 *
 * A file already fetched is reused. The second drag of the same clip is then instant, which is what
 * makes the interaction bearable: the first one pays for the transfer and the rest do not.
 */
export async function fetchToTemp(
	origin: string,
	assetId: string,
	file: LocalFile,
	onProgress: Progress
): Promise<string | null> {
	const at = arrivingAt(assetId, file);
	if (fs.existsSync(at.finished)) return at.finished;
	const url = outgoingUrl(origin, assetId, file);
	const headers = await signedInHeaders(origin);
	return transfers().start(url, headers, at, file.size_bytes, onProgress);
}
