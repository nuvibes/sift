/* The transfer itself: the file it writes, and the two markers a drag depends on. */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { reply } from '../test/electron-stub';
import { type Arriving, type Fetch, markerFiles, markFailed, transferTo } from './transfer';

let temp: string;
let at: Arriving;

beforeEach(() => {
	temp = fs.mkdtempSync(path.join(os.tmpdir(), 'sift-transfer-'));
	const finished = path.join(temp, 'somewhere', 'clip.mp4');
	at = { partial: `${finished}.partial`, finished };
});

afterEach(() => {
	fs.rmSync(temp, { recursive: true, force: true });
});

const URL_ASKED = 'http://192.168.1.5:5171/api/assets/A/stream';

/** A fetch that answers from a queue, and records what it was asked for. */
function answering(...answers: (Response | Error)[]): Fetch & { asked: string[] } {
	const asked: string[] = [];
	const impl = async (url: string): Promise<Response> => {
		asked.push(url);
		const next = answers.shift();
		if (next instanceof Error) throw next;
		return next ?? reply(null, { ok: false });
	};
	return Object.assign(impl, { asked });
}

/* --- the two markers, which are the whole contract with the drag ------------------------------ */

/* A streamed drag hands the receiving application a stream over this very file while it is still
 * being written. */

describe('the markers', () => {
	it('marks the file done, before it renames it into place', async () => {
		await transferTo(answering(reply('hello')), URL_ASKED, {}, at, 5, () => {});

		// Beside the PARTIAL name, because that is the file the reader opened and followed.
		expect(fs.existsSync(markerFiles(at).done)).toBe(true);
		expect(fs.existsSync(markerFiles(at).failed)).toBe(false);
	});

	it('marks a failure, so a reader is not left waiting out its patience', async () => {
		const written = await transferTo(
			answering(reply(null, { ok: false })),
			URL_ASKED,
			{},
			at,
			5,
			() => {}
		);

		expect(written).toBeNull();
		expect(fs.existsSync(markerFiles(at).failed)).toBe(true);
	});

	it('does not inherit what a previous attempt said about itself', async () => {
		/* A failed attempt leaves a marker. Without clearing it, the next drag of the same clip hands
		 * the receiver a stream that reports failure before a single byte has been asked for. */
		fs.mkdirSync(path.dirname(at.partial), { recursive: true });
		fs.writeFileSync(markerFiles(at).failed, '');

		await transferTo(answering(reply('hello')), URL_ASKED, {}, at, 5, () => {});

		expect(fs.existsSync(markerFiles(at).failed)).toBe(false);
	});

	/* The one failure the transfer cannot report, because there is nothing left of it to report
	 * with. The host leaves this from outside when the process doing the work has died. */
	it('can be marked failed from outside, for a transfer whose process has gone', () => {
		fs.mkdirSync(path.dirname(at.partial), { recursive: true });

		markFailed(at);

		expect(fs.existsSync(markerFiles(at).failed)).toBe(true);
	});

	it('does not throw when the folder it would mark in is already gone', () => {
		expect(() => markFailed(at)).not.toThrow();
	});
});

describe('the transfer', () => {
	it('writes the bytes and answers where they went', async () => {
		const fetchImpl = answering(reply('hello'));

		const written = await transferTo(fetchImpl, URL_ASKED, {}, at, 5, () => {});

		expect(written).toBe(at.finished);
		expect(fs.readFileSync(at.finished, 'utf8')).toBe('hello');
		expect(fetchImpl.asked[0]).toBe(URL_ASKED);
	});

	/* The window's own credential travels with the request, because the process making it is not
	   the one that is signed in. */
	it('sends the headers it was given', async () => {
		const seen: Record<string, string>[] = [];
		const fetchImpl: Fetch = async (_url, init) => {
			seen.push(init.headers);
			return reply('hello');
		};

		await transferTo(fetchImpl, URL_ASKED, { Cookie: 'sift=abc' }, at, 5, () => {});

		expect(seen[0]).toEqual({ Cookie: 'sift=abc' });
	});

	/* The whole reason a copy is kept. The first drag pays for the transfer; every one after it is
	 * instant, which is what makes the interaction bearable at all. */
	it('reuses a file it already has rather than fetching twice', async () => {
		const fetchImpl = answering(reply('hello'));
		const first = await transferTo(fetchImpl, URL_ASKED, {}, at, 5, () => {});

		const second = await transferTo(fetchImpl, URL_ASKED, {}, at, 5, () => {});

		expect(second).toBe(first);
		expect(fetchImpl.asked).toHaveLength(1);
	});

	/* A fetch that dies half way must leave NOTHING a cache check would accept. */
	it('leaves nothing behind when the transfer fails', async () => {
		const dies = {
			ok: true,
			body: new ReadableStream<Uint8Array>({
				start(controller) {
					controller.enqueue(new TextEncoder().encode('half'));
					controller.error(new Error('the network went away'));
				}
			})
		} as unknown as Response;

		expect(await transferTo(answering(dies), URL_ASKED, {}, at, 5, () => {})).toBeNull();
		expect(fs.existsSync(at.partial)).toBe(false);
		expect(fs.existsSync(at.finished)).toBe(false);

		const retried = await transferTo(answering(reply('whole')), URL_ASKED, {}, at, 5, () => {});
		expect(fs.readFileSync(retried as string, 'utf8')).toBe('whole');
	});

	/* NOTHING, and not only at the moment it gives up. */
	it('leaves nothing behind a moment after the transfer fails either', async () => {
		const dies = {
			ok: true,
			body: new ReadableStream<Uint8Array>({
				start(controller) {
					controller.error(new Error('the network went away'));
				}
			})
		} as unknown as Response;

		expect(await transferTo(answering(dies), URL_ASKED, {}, at, 5, () => {})).toBeNull();
		await new Promise((resolve) => setTimeout(resolve, 150));

		expect(fs.existsSync(at.partial)).toBe(false);
		expect(fs.existsSync(at.finished)).toBe(false);
	});

	/* The partial cannot be opened, and the first bytes arrive after the stream has already failed
	 * and closed itself: giving up must not wait for a close that has been and gone. */
	it('gives up on a stream that closed itself before the first bytes arrived', async () => {
		const unopenable = { ...at, partial: path.join(temp, 'gone', 'clip.mp4.partial') };
		const late = {
			ok: true,
			body: new ReadableStream<Uint8Array>({
				async pull(controller) {
					await new Promise((resolve) => setTimeout(resolve, 100));
					controller.enqueue(new TextEncoder().encode('late'));
				}
			})
		} as unknown as Response;

		const outcome = await Promise.race([
			transferTo(answering(late), URL_ASKED, {}, unopenable, 4, () => {}),
			new Promise((resolve) => setTimeout(() => resolve('still waiting'), 2000))
		]);

		expect(outcome).toBeNull();
		expect(fs.existsSync(unopenable.finished)).toBe(false);
	});

	it('says how far along it is, and finishes on the real total', async () => {
		const reports: [number, number | null][] = [];

		await transferTo(answering(reply('hello')), URL_ASKED, {}, at, 5, (received, total) =>
			reports.push([received, total])
		);

		expect(reports.at(-1)).toEqual([5, 5]);
	});

	it('answers nothing when the server cannot be reached', async () => {
		const written = await transferTo(
			answering(new Error('ECONNREFUSED')),
			URL_ASKED,
			{},
			at,
			5,
			() => {}
		);

		expect(written).toBeNull();
		expect(fs.existsSync(markerFiles(at).failed)).toBe(true);
	});
});
