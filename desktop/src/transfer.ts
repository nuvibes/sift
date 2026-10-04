/* Fetching an asset's bytes to a local file, in a process that is not the main one.
 *
 * ## Why this is its own module, and why it has no Electron in it
 *
 * A streamed drag hands the receiving application a stream over a file Sift is still writing. The
 * receiver reads that stream through COM, and COM delivers those reads to the thread that created
 * the object: the main thread. So while a drop is being taken, the main thread sits inside a
 * read, waiting for bytes. If the bytes were produced by the main thread's own event loop, that
 * wait could never end: a deadlock on every drag, with both applications frozen.
 *
 * So the transfer runs in a utility process: its own event loop, which nothing the drag does can
 * starve. This file is the part that runs there, and it is free of Electron imports because a
 * utility process is a Node environment with no `app`, no `session` and no `BrowserWindow`.
 *
 * ## What that costs
 *
 * The credential. The cookie has to be read in the main process and handed over, which is a
 * secret crossing a process boundary. Both processes are Sift's own and the utility process
 * runs no remote content, so it is a boundary inside one application rather than one between
 * parties.
 *
 * And transport trust: Node's `fetch` trusts the operating system's list of certificate
 * authorities, where the window's session would also honour anything the shell had been told to
 * accept. Client mode over a local network is plain HTTP, so this is not reached today; a server
 * behind a private authority would meet it.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';

/** Where a fetch writes, where it ends up, and how the reader alongside it knows which. */
export interface Arriving {
	/** The file being written, which a reader may open and follow while it grows. */
	partial: string;
	/** Where it is renamed to when it is complete. */
	finished: string;
}

/** How a fetch is getting on, so the window can say something while a large file comes across. */
export type Progress = (received: number, total: number | null) => void;

/* How a reader following the file is told the transfer has ENDED, and how it ended.
 *
 * Two empty files beside the one being written, and nothing cleverer. A reader that has caught up
 * with the writer cannot tell "there is no more yet" from "there is no more at all" by looking at
 * the file (both are a read that returns nothing), and it is on the other side of a process
 * boundary, so it cannot be told directly. A socket or a pipe between the two would be a port to
 * reach and a handle to leak; a file that either exists or does not is neither.
 *
 * The failure marker matters more than the success one: without it a transfer that died leaves a
 * reader waiting out its whole patience before giving up, and the file it finally hands over is
 * whatever had arrived. */
export function markerFiles(at: Arriving): { done: string; failed: string } {
	return { done: `${at.partial}.done`, failed: `${at.partial}.failed` };
}

/**
 * Leave a marker, and never fail because of it.
 *
 * The markers are how a reader following a partial file tells "no more yet" from "no more at all",
 * so writing one is worth attempting on every path out of a transfer. It is also the LAST thing
 * that can be done about a transfer that has already gone wrong, and the commonest reason it
 * cannot be done is that the folder it would go in has been taken away, which is the same reason
 * the transfer failed.
 */
export function mark(file: string): void {
	try {
		fs.writeFileSync(file, '');
	} catch {
		/* Nothing to do and nothing to say: a reader that cannot see this folder cannot see the
		 * file it describes either, so there is no one left to tell. */
	}
}

/**
 * Say a transfer has failed, from outside it.
 *
 * The host calls this when the process doing the work has DIED, which is the one failure the
 * transfer itself cannot report, because there is nothing left of it to report with. Without it a
 * receiver waits out its full patience on a file that stopped growing for good.
 */
export function markFailed(at: Arriving): void {
	mark(markerFiles(at).failed);
}

/* How often the caller is told about a download's progress.
 *
 * Per chunk would be thousands of messages for a large file, each one crossing a process boundary
 * and landing on the page as a state change. Twice a second is often enough to look live and rare
 * enough to cost nothing. */
const PROGRESS_INTERVAL_MS = 500;

/** How a request is made. Injected so a test can answer without a network. */
export type Fetch = (url: string, init: { headers: Record<string, string> }) => Promise<Response>;

/**
 * Fetch bytes to `at.partial` and rename to `at.finished`. The finished path, or null.
 *
 * NO SIZE CAP, deliberately: a clip is whatever size it is, and refusing the large ones would make
 * the feature unavailable exactly where dragging by hand is most tedious.
 */
export async function transferTo(
	fetchImpl: Fetch,
	url: string,
	headers: Record<string, string>,
	at: Arriving,
	total: number | null,
	onProgress: Progress
): Promise<string | null> {
	if (fs.existsSync(at.finished)) return at.finished;
	// The folder FIRST, before anything is written into it, including the marker that says this
	// failed. A failure before the folder existed could not record itself, so a reader following the
	// file would have waited out its whole patience for a transfer that had already given up.
	fs.mkdirSync(path.dirname(at.finished), { recursive: true });
	const marks = markerFiles(at);
	// Whatever a previous attempt at this file said about itself is not about this one.
	fs.rmSync(marks.done, { force: true });
	fs.rmSync(marks.failed, { force: true });

	let response: Response;
	try {
		response = await fetchImpl(url, { headers });
	} catch {
		mark(marks.failed);
		return null;
	}
	if (!response.ok || response.body === null) {
		mark(marks.failed);
		return null;
	}

	/* Written beside the real name and renamed at the end, so a fetch interrupted half way cannot
	 * leave a truncated file that the cache check above would then hand out as complete. */
	const out = fs.createWriteStream(at.partial);
	/* A write stream reports a failure to OPEN as an 'error' EVENT rather than through the write
	 * callback, and an 'error' with no listener is an uncaught exception. The failure still reaches
	 * the loop below through the callback, so this listener exists to make the event handled. */
	out.on('error', () => {});
	let received = 0;
	let announced = 0;

	try {
		for await (const chunk of streamOf(response.body)) {
			await write(out, chunk);
			received += chunk.byteLength;
			const now = Date.now();
			if (now - announced >= PROGRESS_INTERVAL_MS) {
				announced = now;
				onProgress(received, total);
			}
		}
	} catch {
		/* Closed BEFORE it is cleared up. The stream asked for its file when it was made, and that
		 * open may still be on its way: destroyed and left, it lands after the removal below and
		 * creates the partial again, empty, and under a retry it empties the file the retry has
		 * just written, which is then renamed into place as a finished file of no bytes. */
		await closed(out);
		fs.rmSync(at.partial, { force: true });
		// Written BEFORE returning, and this is the one that a drag depends on. A reader following
		// this file has no other way to learn that the transfer died: to it, a dead writer and a slow
		// one look exactly the same.
		mark(marks.failed);
		return null;
	}
	await new Promise<void>((resolve) => out.end(resolve));
	// The marker BEFORE the rename, deliberately. A reader that has caught up needs to know the file
	// is complete, and it needs to know it whether or not it notices the rename.
	mark(marks.done);
	fs.renameSync(at.partial, at.finished);
	onProgress(received, total);
	return at.finished;
}

/** The response body as something `for await` can walk. */
async function* streamOf(body: ReadableStream<Uint8Array>): AsyncGenerator<Uint8Array> {
	const reader = body.getReader();
	try {
		for (;;) {
			const { done, value } = await reader.read();
			if (done) return;
			if (value !== undefined) yield value;
		}
	} finally {
		reader.releaseLock();
	}
}

/** The stream destroyed, and its file handle (or the open still on its way) finished with. */
export function closed(out: fs.WriteStream): Promise<void> {
	return new Promise((resolve) => {
		if (out.closed) {
			resolve();
			return;
		}
		out.once('close', () => resolve());
		out.destroy();
	});
}

/** One chunk out, respecting back-pressure: without this a fast network fills memory with a
 *  queue of pending writes rather than a file. */
function write(out: fs.WriteStream, chunk: Uint8Array): Promise<void> {
	return new Promise((resolve, reject) => {
		out.write(chunk, (error) => (error ? reject(error) : resolve()));
	});
}
