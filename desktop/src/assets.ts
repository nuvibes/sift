/* Getting at an asset's actual file, on behalf of the window. */

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

/* How long a share is given to answer before the drag stops waiting on it. */
const SHARE_TIMEOUT_MS = 1500;

/** The share path, if THIS machine can reach it and it is the file the server meant. */
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

/* Everything fetched for a drag lands here, under the operating system's own temp folder. */
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

/* A name that is only ever a name. It arrives from the server, which in client mode is another
 * machine, so it is untrusted input that this module is about to use as a filename. */
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
		/* No network, a server that has gone away, a reply that is not JSON. */
		return null;
	}
}

/* Checked, not cast. The answer decides what path is handed to the native drag and what name a
 * file is written under, and in client mode it comes from another computer. */
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

/** The two names a fetch of this asset uses. Computed without touching the network or the disk. */
export function arrivingAt(assetId: string, file: LocalFile): Arriving {
	/* A copy without a place is kept apart from one of the original, so a copy fetched before
	   the file held a place can never be handed to a drag that must carry none. */
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

/* What the window is signed in with, as a header a plain HTTP client can send. */
async function signedInHeaders(origin: string): Promise<Record<string, string>> {
	try {
		const held = await session.defaultSession.cookies.get({ url: origin });
		if (held.length === 0) return {};
		return { Cookie: held.map((one) => `${one.name}=${one.value}`).join('; ') };
	} catch {
		/* Nothing to send is not an error to raise here. */
		return {};
	}
}

/** Where a drag's bytes come from. */
export function outgoingUrl(origin: string, assetId: string, file: LocalFile): string {
	const id = encodeURIComponent(assetId);
	return file.holds_a_place === true
		? `${origin}/api/assets/${id}/outgoing`
		: `${origin}/api/assets/${id}/stream`;
}

/** Fetch an asset's bytes to a local file, so there is something to drag. */
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
