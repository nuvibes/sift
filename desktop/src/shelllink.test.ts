/* The link the backend asks this shell through: who may ask, and what each act does with a word. */

import { afterEach, describe, expect, it, vi } from 'vitest';

import type { FirewallReport } from './firewall';
import {
	act,
	carriesToken,
	openShellLink,
	type Deferred,
	type ShellActs,
	type ShellLink
} from './shelllink';

const OPEN: FirewallReport = { state: 'open', networks: ['Private'], scope: 'private' };

/** Every act the fake was asked, in order, with what it was handed; and what ran after an answer. */
interface Heard {
	written: boolean[];
	scopes: string[];
	asked: [string, ...unknown[]][];
	ran: string[];
}

/** An act that is answered first and runs after, recording both. */
function later<T>(ran: string[], name: string, answer: T): Promise<Deferred<T>> {
	return Promise.resolve({
		answer,
		after: async () => {
			ran.push(name);
		}
	});
}

function fakeActs(): ShellActs & Heard {
	const written: boolean[] = [];
	const scopes: string[] = [];
	const asked: [string, ...unknown[]][] = [];
	const ran: string[] = [];
	let starting = false;
	return {
		written,
		scopes,
		asked,
		ran,
		setSharing: (on) => {
			asked.push(['sharing', on]);
			return later(ran, 'sharing', { ok: true, refusal: null });
		},
		storage: async () => ({
			dataDir: 'C:\\Lib\\data',
			cacheDir: 'C:\\Lib\\cache',
			dataBytes: 1,
			cacheBytes: 2,
			lastMove: null
		}),
		moveStorage: (folder) => {
			asked.push(['move', folder]);
			return later(ran, 'move', { ok: true, refusal: null });
		},
		update: (...given: unknown[]) => {
			asked.push(['update', ...given]);
			return later(ran, 'update', { ok: true as const, version: '0.1.300' });
		},
		log: (lines) => {
			asked.push(['log', lines]);
			return { lines: ['one'], path: 'C:\\Sift\\shell.log', size: 4, present: true };
		},
		libraries: () => ({ current: 'C:\\Lib\\data', libraries: [] }),
		openLibrary: (dataDir) => {
			asked.push(['open', dataDir]);
			return later(ran, 'open', { ok: true, refusal: null });
		},
		machine: () => 'DESK-ONE',
		startup: {
			read: () => starting,
			write: (on: boolean) => {
				written.push(on);
				starting = on;
				return starting;
			}
		},
		sharing: () => ({ enabled: true, live: true, address: 'http://192.168.1.20:5171', port: 5171 }),
		firewall: async () => OPEN,
		openFirewall: async (scope) => {
			scopes.push(scope);
			return { ...OPEN, scope };
		}
	};
}

describe('who may ask', () => {
	it('takes only this launch secret, in the bearer form', () => {
		expect(carriesToken('Bearer abc', 'abc')).toBe(true);
		expect(carriesToken('Bearer abd', 'abc')).toBe(false);
		expect(carriesToken('Bearer abcd', 'abc')).toBe(false);
		expect(carriesToken('abc', 'abc')).toBe(false);
		expect(carriesToken(undefined, 'abc')).toBe(false);
	});
});

describe('the acts', () => {
	it('answers the facts in one read', async () => {
		const done = await act(fakeActs(), 'GET', '/facts', undefined);
		expect(done).toEqual({
			status: 200,
			body: {
				machine: 'DESK-ONE',
				startsWithWindows: false,
				sharing: { enabled: true, live: true, address: 'http://192.168.1.20:5171', port: 5171 }
			}
		});
	});

	it('turns start with Windows on only for a real true', async () => {
		const acts = fakeActs();
		await act(acts, 'PUT', '/start-with-windows', { on: 'yes' });
		await act(acts, 'PUT', '/start-with-windows', { on: true });
		expect(acts.written).toEqual([false, true]);
	});

	it('answers null for start with Windows where this copy cannot register one', async () => {
		const acts = { ...fakeActs(), startup: null };
		expect((await act(acts, 'GET', '/facts', undefined)).body).toMatchObject({
			startsWithWindows: null
		});
		expect(await act(acts, 'PUT', '/start-with-windows', { on: true })).toEqual({
			status: 200,
			body: { startsWithWindows: null }
		});
	});

	it('opens the narrow rule unless the scope is exactly any', async () => {
		const acts = fakeActs();
		await act(acts, 'POST', '/firewall', { scope: 'everything' });
		await act(acts, 'POST', '/firewall', null);
		await act(acts, 'POST', '/firewall', { scope: 'any' });
		expect(acts.scopes).toEqual(['private', 'private', 'any']);
	});

	it('reads the firewall without opening anything', async () => {
		const acts = fakeActs();
		expect(await act(acts, 'GET', '/firewall', undefined)).toEqual({ status: 200, body: OPEN });
		expect(acts.scopes).toEqual([]);
	});

	it('knows no other act', async () => {
		expect((await act(fakeActs(), 'POST', '/restart', {})).status).toBe(404);
		expect((await act(fakeActs(), 'GET', '/start-with-windows', undefined)).status).toBe(404);
	});
});

describe('the listening link', () => {
	let link: ShellLink | null = null;
	afterEach(async () => {
		await link?.close();
		link = null;
	});

	it('listens on this machine only, and refuses a request without the secret', async () => {
		link = await openShellLink(fakeActs(), 'the-secret');
		expect(link.url).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/);
		const refused = await fetch(`${link.url}/facts`);
		expect(refused.status).toBe(401);
		const wrong = await fetch(`${link.url}/facts`, { headers: { authorization: 'Bearer nope' } });
		expect(wrong.status).toBe(401);
	});

	it('answers an act asked with the secret', async () => {
		const acts = fakeActs();
		link = await openShellLink(acts, 'the-secret');
		const done = await fetch(`${link.url}/start-with-windows`, {
			method: 'PUT',
			headers: { authorization: 'Bearer the-secret', 'content-type': 'application/json' },
			body: JSON.stringify({ on: true })
		});
		expect(await done.json()).toEqual({ startsWithWindows: true });
		expect(acts.written).toEqual([true]);
	});

	it('refuses a body too large to be one word, and survives an act that throws', async () => {
		const acts = {
			...fakeActs(),
			firewall: vi.fn(async (): Promise<FirewallReport> => {
				throw new Error('powershell went away');
			})
		};
		link = await openShellLink(acts, 'the-secret');
		const auth = { authorization: 'Bearer the-secret' };
		const failed = await fetch(`${link.url}/firewall`, { headers: auth });
		expect(failed.status).toBe(500);
		const big = await fetch(`${link.url}/firewall`, {
			method: 'POST',
			headers: auth,
			body: 'x'.repeat(4096)
		});
		expect(big.status).toBe(413);
		const unreadable = await fetch(`${link.url}/start-with-windows`, {
			method: 'PUT',
			headers: auth,
			body: 'not json'
		});
		expect(await unreadable.json()).toEqual({ startsWithWindows: false });
	});

	it('makes a fresh secret each launch when none is given', async () => {
		link = await openShellLink(fakeActs());
		expect(link.token).toMatch(/^[0-9a-f]{64}$/);
	});
});

describe('the acts that stop the backend asking', () => {
	it('turns sharing on only for a real true, and answers before it acts', async () => {
		const acts = fakeActs();
		const off = await act(acts, 'PUT', '/sharing', { on: 'yes' });
		await act(acts, 'PUT', '/sharing', { on: true });
		expect(acts.asked).toEqual([
			['sharing', false],
			['sharing', true]
		]);
		expect(off.body).toEqual({ ok: true, refusal: null });
		/* Answered, and nothing run yet: the act waits for the answer to leave. */
		expect(acts.ran).toEqual([]);
		await off.after?.();
		expect(acts.ran).toEqual(['sharing']);
	});

	it('moves only into one named folder', async () => {
		const acts = fakeActs();
		expect((await act(acts, 'POST', '/storage/move', { folder: '' })).status).toBe(400);
		expect((await act(acts, 'POST', '/storage/move', { folder: 7 })).status).toBe(400);
		expect((await act(acts, 'POST', '/storage/move', null)).status).toBe(400);
		const done = await act(acts, 'POST', '/storage/move', { folder: 'E:\\Sift' });
		expect(done.status).toBe(200);
		expect(acts.asked).toEqual([['move', 'E:\\Sift']]);
	});

	it('reads the storage folders and the last move', async () => {
		expect((await act(fakeActs(), 'GET', '/storage', {})).body).toMatchObject({
			dataDir: 'C:\\Lib\\data',
			lastMove: null
		});
	});

	it('installs an update without reading anything the ask names', async () => {
		const acts = fakeActs();
		const done = await act(acts, 'POST', '/update', {
			feed: 'https://elsewhere.example/evil.json',
			version: '9.9.9'
		});
		expect(acts.asked).toEqual([['update']]);
		expect(done.body).toEqual({ ok: true, version: '0.1.300' });
	});

	it('opens only one named library', async () => {
		const acts = fakeActs();
		expect((await act(acts, 'POST', '/libraries/open', { dataDir: '' })).status).toBe(400);
		expect((await act(acts, 'POST', '/libraries/open', {})).status).toBe(400);
		await act(acts, 'POST', '/libraries/open', { dataDir: 'D:\\Other\\data' });
		expect(acts.asked).toEqual([['open', 'D:\\Other\\data']]);
		expect((await act(acts, 'GET', '/libraries', {})).body).toEqual({
			current: 'C:\\Lib\\data',
			libraries: []
		});
	});

	it('reads the end of its own log by a count held to 1 to 2000', async () => {
		const acts = fakeActs();
		await act(acts, 'GET', '/log', { lines: '99999' });
		await act(acts, 'GET', '/log', { lines: 'lots' });
		await act(acts, 'GET', '/log', { lines: '0' });
		await act(acts, 'GET', '/log', {});
		expect(acts.asked).toEqual([
			['log', 2000],
			['log', 200],
			['log', 1],
			['log', 200]
		]);
	});
});

describe('an act answered first, over the listening link', () => {
	let link: ShellLink | null = null;
	afterEach(async () => {
		await link?.close();
		link = null;
	});

	it('sends the answer without waiting on the act, and then runs the act', async () => {
		const order: string[] = [];
		const acts = {
			...fakeActs(),
			/* An act that never finishes, as a backend being stopped never answers: the answer
			   must already be on its way. */
			setSharing: async (): Promise<Deferred<{ ok: boolean; refusal: null }>> => ({
				answer: { ok: true, refusal: null },
				after: () => {
					order.push('acted');
					return new Promise<void>(() => {});
				}
			})
		};
		link = await openShellLink(acts, 'the-secret');
		const reply = await fetch(`${link.url}/sharing`, {
			method: 'PUT',
			headers: { authorization: 'Bearer the-secret', 'content-type': 'application/json' },
			body: JSON.stringify({ on: false })
		});
		expect(await reply.json()).toEqual({ ok: true, refusal: null });
		await new Promise((done) => setTimeout(done, 20));
		expect(order).toEqual(['acted']);
	});

	it('reads the log count from the query, and survives an act after that throws', async () => {
		const acts = {
			...fakeActs(),
			openLibrary: async (): Promise<Deferred<{ ok: boolean; refusal: null }>> => ({
				answer: { ok: true, refusal: null },
				after: async () => {
					throw new Error('the backend would not start');
				}
			})
		};
		link = await openShellLink(acts, 'the-secret');
		const auth = { authorization: 'Bearer the-secret' };
		const opened = await fetch(`${link.url}/libraries/open`, {
			method: 'POST',
			headers: { ...auth, 'content-type': 'application/json' },
			body: JSON.stringify({ dataDir: 'D:\\Other\\data' })
		});
		expect(opened.status).toBe(200);
		await new Promise((done) => setTimeout(done, 20));
		const read = await fetch(`${link.url}/log?lines=5`, { headers: auth });
		expect(await read.json()).toMatchObject({ lines: ['one'] });
		expect(acts.asked).toContainEqual(['log', 5]);
	});
});
