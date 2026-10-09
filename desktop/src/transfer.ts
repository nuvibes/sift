/* Fetching an asset's bytes to a local file, in a process that is not the main one. */

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

/* How a reader following the file is told the transfer has ENDED, and how it ended. */
export function markerFiles(at: Arriving): { done: string; failed: string } {
	return { done: `${at.partial}.done`, failed: `${at.partial}.failed` };
}

/** Leave a marker, and never fail because of it. */
export function mark(file: string): void {
	try {
		fs.writeFileSync(file, '');
	} catch {
		/* Nothing to do and nothing to say: a reader that cannot see this folder cannot see the
		 * file it describes either, so there is no one left to tell. */
	}
}

/** Say a transfer has failed, from outside it. */
export function markFailed(at: Arriving): void {
	mark(markerFiles(at).failed);
}

/* How often the caller is told about a download's progress. */
const PROGRESS_INTERVAL_MS = 500;

/** How a request is made. Injected so a test can answer without a network. */
export type Fetch = (url: string, init: { headers: Record<string, string> }) => Promise<Response>;

/** Fetch bytes to `at.partial` and rename to `at.finished`. */
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
	// failed.
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
	 * callback, and an 'error' with no listener is an uncaught exception. */
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
		/* Closed BEFORE it is cleared up. */
		await closed(out);
		fs.rmSync(at.partial, { force: true });
		// Written BEFORE returning, and this is the one that a drag depends on.
		mark(marks.failed);
		return null;
	}
	await new Promise<void>((resolve) => out.end(resolve));
	// The marker BEFORE the rename, deliberately. A reader that has caught up needs to know the
	// file is complete, and it needs to know it whether or not it notices the rename.
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
