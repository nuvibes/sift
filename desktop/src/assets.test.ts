/* Getting at an asset's file, and above all what happens when the library is somewhere else.
 *
 * The behaviour worth pinning down here is not "does it download". It is the three things that
 * would each be a real fault and would each look like nothing at all: a name from another machine
 * used unsanitised as a filename, a half-arrived file left behind where the next drag would hand it
 * over as complete, and a fetch that keeps running after its window has gone.
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/* The pool is never the real one: a test must not fork a process, and what is worth asserting here
 * is what this module ASKS FOR: the address, the two names, and the size it promises. */
const started: {
	url: string;
	headers: Record<string, string>;
	at: Arriving;
	total: number | null;
}[] = vi.hoisted(() => []);

vi.mock('./transfers', () => ({
	transfers: () => ({
		start: async (
			url: string,
			headers: Record<string, string>,
			at: { partial: string; finished: string },
			total: number | null
		) => {
			started.push({ url, headers, at, total });
			return at.finished;
		}
	})
}));

import {
	fetchAnswers,
	fetched,
	paths,
	reply,
	resetElectronStub,
	session
} from '../test/electron-stub';
import type { Arriving } from './transfer';
import {
	alreadyFetched,
	arrivingAt,
	clearFetchedFiles,
	fetchToTemp,
	localFile,
	reachedHere,
	safeName
} from './assets';

const ORIGIN = 'http://192.168.1.5:5171';
const AN_ASSET = '01HX0000000000000000000007';

let temp: string;

beforeEach(() => {
	resetElectronStub();
	started.length = 0;
	temp = fs.mkdtempSync(path.join(os.tmpdir(), 'sift-assets-'));
	paths.temp = temp;
});

afterEach(() => {
	fs.rmSync(temp, { recursive: true, force: true });
});

describe('safeName', () => {
	/* THE NAME COMES FROM ANOTHER MACHINE. In client mode the server that answers is somebody
	 * else's, so what it calls a file is untrusted input about to be used as a path. */
	it('leaves a name that was trying to be a path unable to leave the folder', () => {
		for (const trying of ['..\\..\\Startup\\evil.lnk', '../../etc/passwd', '..', '.']) {
			const safe = safeName(trying);
			expect(safe).not.toContain('/');
			expect(safe).not.toContain('\\');
			expect(['.', '..']).not.toContain(safe);
			expect(safe.length).toBeGreaterThan(0);
		}
	});

	it('keeps an ordinary name as it is', () => {
		expect(safeName('holiday clip 2.mp4')).toBe('holiday clip 2.mp4');
	});

	/* A name that sanitises away to nothing still has to be a filename, or the write goes to the
	 * directory itself. */
	it('answers something usable for a name made only of punctuation', () => {
		expect(safeName('...')).toBe('file');
		expect(safeName('')).toBe('file');
		// A RUN of unsafe characters collapses to one underscore rather than one each: the pattern
		// matches greedily, which keeps a name readable instead of turning it into a row of them.
		expect(safeName('///')).toBe('_');
	});
});

describe('localFile', () => {
	it('asks the origin of the page that is asking', async () => {
		fetchAnswers.push(reply({ path: 'D:\\Media\\a.mp4', filename: 'a.mp4', size_bytes: 10 }));

		const answered = await localFile(ORIGIN, AN_ASSET);

		expect(fetched[0]).toBe(`${ORIGIN}/api/assets/${AN_ASSET}/local-file`);
		expect(answered?.path).toBe('D:\\Media\\a.mp4');
	});

	/* Every failure is the same answer. A server that has gone away, a signed-out session, an asset
	 * this viewer may not see: none of them is worth an exception crossing into the drag handler,
	 * and all of them mean the same thing: there is nothing to drag. */
	it('answers nothing when the server refuses', async () => {
		fetchAnswers.push(reply(null, { ok: false }));
		expect(await localFile(ORIGIN, AN_ASSET)).toBeNull();
	});

	it('answers nothing when the server cannot be reached at all', async () => {
		fetchAnswers.push(new Error('ECONNREFUSED'));
		expect(await localFile(ORIGIN, AN_ASSET)).toBeNull();
	});

	/* An answer is checked, not cast: one with no `path` would otherwise reach the native drag as
	 * a path of `undefined`, and `undefined !== null` reads as "a local file".
	 */
	it('answers nothing for an answer of the wrong shape', async () => {
		for (const wrong of [
			'data',
			[],
			{ filename: 'a.mp4', size_bytes: 10 },
			{ path: 7, filename: 'a.mp4', size_bytes: 10 },
			{ path: null, filename: 'a.mp4', size_bytes: '10' },
			{ path: null, shared_path: 7, filename: 'a.mp4', size_bytes: 10 },
			{ path: null, size_bytes: 10 }
		]) {
			fetchAnswers.push(reply(wrong));
			expect(await localFile(ORIGIN, AN_ASSET), JSON.stringify(wrong)).toBeNull();
		}
	});

	it('takes a share path when the server names one', async () => {
		fetchAnswers.push(
			reply({ path: null, shared_path: '\\\\nas\\a.mp4', filename: 'a.mp4', size_bytes: null })
		);
		expect((await localFile(ORIGIN, AN_ASSET))?.shared_path).toBe('\\\\nas\\a.mp4');
	});
});

describe('fetchToTemp', () => {
	const remote = { path: null, filename: 'clip.mp4', size_bytes: 5 };

	/* The transfer itself is tested in transfer.test.ts, where it lives. What is left here is the
	 * part this module still decides: where the file goes, and whether it has to be fetched at all.
	 */

	it('names the file under the temporary folder, from the asset and the sanitised name', async () => {
		await fetchToTemp(ORIGIN, AN_ASSET, remote, () => {});

		expect(started).toHaveLength(1);
		expect(started[0].url).toBe(`${ORIGIN}/api/assets/${AN_ASSET}/stream`);
		expect(started[0].at.finished).toBe(path.join(temp, 'sift-drag', AN_ASSET, 'clip.mp4'));
		expect(started[0].at.partial).toBe(`${started[0].at.finished}.partial`);
		expect(started[0].total).toBe(5);
	});

	/* A file that says where it was made is fetched through the door that takes the place out,
	 * and kept apart from any copy of the original. */
	it('fetches a file holding a place from the outgoing door, into a folder of its own', async () => {
		await fetchToTemp(ORIGIN, AN_ASSET, { ...remote, holds_a_place: true }, () => {});

		expect(started).toHaveLength(1);
		expect(started[0].url).toBe(`${ORIGIN}/api/assets/${AN_ASSET}/outgoing`);
		expect(started[0].at.finished).toBe(
			path.join(temp, 'sift-drag', `${AN_ASSET}-unplaced`, 'clip.mp4')
		);
	});

	/* The window's own sign-in for THAT server, so a private library answers the transfer; and none
	 * at all when the session cannot be read, which the server answers as any anonymous caller. */
	it('sends the sign-in this window holds for the server it fetches from', async () => {
		const asked: unknown[] = [];
		vi.spyOn(session.defaultSession.cookies, 'get').mockImplementationOnce(async (...args: unknown[]) => {
			asked.push(args[0]);
			return [
				{ name: 'sift_session', value: 'abc' },
				{ name: 'sift_csrf', value: 'def' }
			] as never;
		});

		await fetchToTemp(ORIGIN, AN_ASSET, remote, () => {});

		expect(asked).toEqual([{ url: ORIGIN }]);
		expect(started[0].headers).toEqual({ Cookie: 'sift_session=abc; sift_csrf=def' });
	});

	it('fetches without a sign-in when the session cannot be read', async () => {
		vi.spyOn(session.defaultSession.cookies, 'get').mockImplementationOnce(async () => {
			throw new Error('the session is gone');
		});

		await fetchToTemp(ORIGIN, AN_ASSET, remote, () => {});

		expect(started).toHaveLength(1);
		expect(started[0].headers).toEqual({});
	});

	/* The whole reason a copy is kept. The first drag pays for the transfer; every one after it is
	 * instant, and nothing is started at all. */
	it('answers with the file already here rather than starting a transfer', async () => {
		const at = arrivingAt(AN_ASSET, remote);
		fs.mkdirSync(path.dirname(at.finished), { recursive: true });
		fs.writeFileSync(at.finished, 'hello');

		expect(await fetchToTemp(ORIGIN, AN_ASSET, remote, () => {})).toBe(at.finished);
		expect(started).toHaveLength(0);
	});

	it('says nothing is here before anything has been fetched', () => {
		expect(alreadyFetched(AN_ASSET, remote)).toBeNull();
	});
});

describe('clearFetchedFiles', () => {
	it('throws away what a previous run left', () => {
		const at = arrivingAt(AN_ASSET, remoteFile());
		fs.mkdirSync(path.dirname(at.finished), { recursive: true });
		fs.writeFileSync(at.finished, 'hello');

		clearFetchedFiles();

		expect(fs.existsSync(at.finished)).toBe(false);
	});

	/* Called before every window exists, including the very first launch on a new machine. */
	it('is content when there is nothing to clear', () => {
		expect(() => clearFetchedFiles()).not.toThrow();
	});
});

function remoteFile() {
	return { path: null, filename: 'clip.mp4', size_bytes: 5 };
}

/* The file on a share both machines can see.
 *
 * This is the path that makes a drag on a second computer cost nothing, so the tests worth having
 * are the ones about NOT taking it: a share this machine cannot reach, and (the serious one) a
 * share that answers with a DIFFERENT file. A share can be mounted from another server under the
 * same name, and a confident drag of the wrong video into a chat window is the worst thing this
 * feature could do.
 */
describe('reachedHere', () => {
	function shared(over: Partial<Parameters<typeof reachedHere>[0]> = {}) {
		return { path: null, shared_path: null, filename: 'clip.mp4', size_bytes: 5, ...over };
	}

	it('answers the path when the file is there and is the size the server described', async () => {
		const onShare = path.join(temp, 'clip.mp4');
		fs.writeFileSync(onShare, 'hello');

		expect(await reachedHere(shared({ shared_path: onShare }))).toBe(onShare);
	});

	it('answers nothing when the server did not offer one', async () => {
		expect(await reachedHere(shared())).toBeNull();
		expect(await reachedHere(shared({ shared_path: '' }))).toBeNull();
		expect(await reachedHere({ path: null, filename: 'clip.mp4', size_bytes: 5 })).toBeNull();
	});

	it('answers nothing when this machine cannot reach the share', async () => {
		expect(await reachedHere(shared({ shared_path: path.join(temp, 'not-here.mp4') }))).toBeNull();
	});

	/* THE ONE THAT MATTERS. Same name, different file: the drag falls back to fetching a copy,
	   which is slower and certainly right. */
	it('refuses a file of the wrong size rather than dragging it', async () => {
		const onShare = path.join(temp, 'clip.mp4');
		fs.writeFileSync(onShare, 'a different video entirely');

		expect(await reachedHere(shared({ shared_path: onShare }))).toBeNull();
	});

	it('takes it on trust when the server never recorded a size', async () => {
		const onShare = path.join(temp, 'clip.mp4');
		fs.writeFileSync(onShare, 'hello');

		expect(await reachedHere(shared({ shared_path: onShare, size_bytes: null }))).toBe(onShare);
	});

	it('answers nothing for a directory that happens to carry the name', async () => {
		const asFolder = path.join(temp, 'clip.mp4');
		fs.mkdirSync(asFolder);

		expect(await reachedHere(shared({ shared_path: asFolder }))).toBeNull();
	});
});
