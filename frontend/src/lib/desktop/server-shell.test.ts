/* Reaching the computer running Sift through its API: which route each ask goes to, and the
 * restart's wait for a DIFFERENT run of the server before the page is loaded again. */

import { afterEach, describe, expect, it, vi } from 'vitest';

const get = vi.hoisted(() => vi.fn());
const put = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());
const serverBootId = vi.hoisted(() => vi.fn());
const machineName = vi.hoisted(() => vi.fn());

vi.mock('$lib/api/client', async (actual) => ({
	...(await actual<typeof import('$lib/api/client')>()),
	api: { get, put, post }
}));
vi.mock('$lib/shell/health', () => ({ serverBootId }));
vi.mock('$lib/bridge', () => ({ bridge: { machineName } }));

import { ApiError } from '$lib/api/client';
import {
	actThere,
	moveServerStorage,
	offersServer,
	openServerFirewall,
	openServerLibrary,
	readServerAppLog,
	readServerDesktop,
	readServerFirewall,
	readServerLibraries,
	readServerStorage,
	restartServer,
	setServerSharing,
	setServerStartsWithWindows,
	thisDevice,
	updateServer
} from './server-shell';

afterEach(() => vi.resetAllMocks());

describe('the asks', () => {
	it('reads the computer running Sift, and answers null where it cannot be read', async () => {
		get.mockResolvedValueOnce({ has_app: true });
		expect(await readServerDesktop()).toEqual({ has_app: true });
		expect(get).toHaveBeenCalledWith('/desktop');

		get.mockRejectedValueOnce(new Error('403'));
		expect(await readServerDesktop()).toBeNull();
		get.mockRejectedValueOnce(new Error('503'));
		expect(await readServerFirewall()).toBeNull();
	});

	it('acts only where an app runs Sift there', () => {
		expect(offersServer(null)).toBe(false);
		expect(
			offersServer({ has_app: false, machine: null, starts_with_windows: null, sharing: null })
		).toBe(false);
		expect(
			offersServer({ has_app: true, machine: 'DESK-ONE', starts_with_windows: null, sharing: null })
		).toBe(true);
	});

	it('sends one word each, to the server', async () => {
		await setServerStartsWithWindows(true);
		await openServerFirewall('any');
		expect(put).toHaveBeenCalledWith('/desktop/start-with-windows', { body: { on: true } });
		expect(post).toHaveBeenCalledWith('/desktop/firewall', { body: { scope: 'any' } });
	});
});

describe('the restart', () => {
	it('waits for a different run, then loads the page again', async () => {
		serverBootId
			.mockResolvedValueOnce('run-1')
			.mockResolvedValueOnce(null)
			.mockResolvedValueOnce('run-1')
			.mockResolvedValueOnce('run-2');
		post.mockResolvedValue({ restarting: true });
		const arrive = vi.fn();

		const outcome = await restartServer('cannot', 'slow', { pollMs: 1, limitMs: 1000, arrive });

		expect(post).toHaveBeenCalledWith('/performance/restart');
		expect(outcome).toEqual({ ok: true });
		expect(arrive).toHaveBeenCalledOnce();
		expect(serverBootId).toHaveBeenCalledTimes(4);
	});

	it('says so when the run never changes', async () => {
		serverBootId.mockResolvedValue('run-1');
		post.mockResolvedValue({ restarting: true });
		const arrive = vi.fn();

		const outcome = await restartServer('cannot', 'slow', { pollMs: 1, limitMs: 20, arrive });

		expect(outcome).toEqual({ ok: false, problem: 'slow' });
		expect(arrive).not.toHaveBeenCalled();
	});

	it('says the server refusal in its own words, and a failure without one in the given words', async () => {
		serverBootId.mockResolvedValue('run-1');
		post.mockRejectedValueOnce(new ApiError(409, 'Conflict', 'Nothing is watching this copy.'));
		expect(await restartServer('cannot', 'slow')).toEqual({
			ok: false,
			problem: 'Nothing is watching this copy.'
		});
		post.mockRejectedValueOnce(new TypeError('network'));
		expect(await restartServer('cannot', 'slow')).toEqual({ ok: false, problem: 'cannot' });
	});
});

describe('the acts that restart Sift there', () => {
	it('asks each at its own route, naming only what the app there checks again', async () => {
		put.mockResolvedValue({ ok: true, refusal: null });
		post.mockResolvedValue({ ok: true, refusal: null });
		get.mockResolvedValue({});
		await setServerSharing(false);
		await moveServerStorage('E:\\Sift');
		await updateServer();
		await openServerLibrary('D:\\Other\\data');
		await readServerAppLog(500);
		await readServerStorage();
		await readServerLibraries();
		expect(put).toHaveBeenCalledWith('/desktop/sharing', { body: { on: false } });
		expect(post).toHaveBeenCalledWith('/desktop/storage/move', { body: { folder: 'E:\\Sift' } });
		expect(post).toHaveBeenCalledWith('/desktop/update');
		expect(post).toHaveBeenCalledWith('/desktop/libraries/open', {
			body: { data_dir: 'D:\\Other\\data' }
		});
		expect(get).toHaveBeenCalledWith('/desktop/log', { query: { lines: '500' } });
		expect(get).toHaveBeenCalledWith('/desktop/storage');
		expect(get).toHaveBeenCalledWith('/desktop/libraries');
	});

	it('reads null where the storage or the libraries cannot be read', async () => {
		get.mockRejectedValue(new Error('409'));
		expect(await readServerStorage()).toBeNull();
		expect(await readServerLibraries()).toBeNull();
	});

	it('waits for a new run once the app there has taken the act on', async () => {
		serverBootId.mockResolvedValueOnce('run-1').mockResolvedValueOnce('run-2');
		const arrive = vi.fn();

		const outcome = await actThere(async () => ({ ok: true, refusal: null }), 'cannot', 'slow', {
			pollMs: 1,
			limitMs: 1000,
			arrive
		});

		expect(outcome).toEqual({ ok: true });
		expect(arrive).toHaveBeenCalledOnce();
	});

	it('waits for nothing when the app there refuses, and says its words', async () => {
		serverBootId.mockResolvedValue('run-1');
		const arrive = vi.fn();

		const outcome = await actThere(
			async () => ({ ok: false, refusal: 'That folder is not empty.' }),
			'cannot',
			'slow',
			{ pollMs: 1, limitMs: 1000, arrive }
		);

		expect(outcome).toEqual({ ok: false, refused: true, problem: 'That folder is not empty.' });
		expect(serverBootId).toHaveBeenCalledOnce();
		expect(arrive).not.toHaveBeenCalled();
	});

	it('says a server refusal, a failure and a wait that ran out, each in its words', async () => {
		serverBootId.mockResolvedValue('run-1');
		const refused = async () => {
			throw new ApiError(409, 'Conflict', "isn't running in the Sift app");
		};
		expect(await actThere(refused, 'cannot', 'slow')).toEqual({
			ok: false,
			refused: true,
			problem: "isn't running in the Sift app"
		});
		const failed = async () => {
			throw new TypeError('network');
		};
		expect(await actThere(failed, 'cannot', 'slow')).toEqual({
			ok: false,
			refused: true,
			problem: 'cannot'
		});
		const taken = async () => ({ ok: true, refusal: null });
		expect(await actThere(taken, 'cannot', 'slow', { pollMs: 1, limitMs: 10 })).toEqual({
			ok: false,
			refused: false,
			problem: 'slow'
		});
	});
});

describe('which device asked', () => {
	it("names the app's own computer on every act that changes the one running Sift", async () => {
		machineName.mockResolvedValue('LAPTOP-TWO');
		put.mockResolvedValue({ ok: true, refusal: null });
		post.mockResolvedValue({ ok: true, refusal: null });
		serverBootId.mockResolvedValue('run-1');
		const device = { device: 'LAPTOP-TWO' };

		await setServerStartsWithWindows(false);
		await openServerFirewall('private');
		await setServerSharing(true);
		await moveServerStorage('E:\\Sift');
		await updateServer();
		await openServerLibrary('D:\\Other\\data');
		await restartServer('cannot', 'slow', { pollMs: 1, limitMs: 5 });

		expect(put).toHaveBeenCalledWith('/desktop/start-with-windows', {
			body: { on: false },
			query: device
		});
		expect(post).toHaveBeenCalledWith('/desktop/firewall', {
			body: { scope: 'private' },
			query: device
		});
		expect(put).toHaveBeenCalledWith('/desktop/sharing', { body: { on: true }, query: device });
		expect(post).toHaveBeenCalledWith('/desktop/storage/move', {
			body: { folder: 'E:\\Sift' },
			query: device
		});
		expect(post).toHaveBeenCalledWith('/desktop/update', { query: device });
		expect(post).toHaveBeenCalledWith('/desktop/libraries/open', {
			body: { data_dir: 'D:\\Other\\data' },
			query: device
		});
		expect(post).toHaveBeenCalledWith('/performance/restart', { query: device });
	});

	it('names nothing in a browser, or where the app cannot say', async () => {
		machineName.mockRejectedValueOnce(new Error('no bridge'));
		expect(await thisDevice()).toBeNull();
		machineName.mockResolvedValueOnce(null);
		put.mockResolvedValue({ ok: true, refusal: null });
		await setServerSharing(false);
		expect(put).toHaveBeenCalledWith('/desktop/sharing', { body: { on: false } });
	});
});
