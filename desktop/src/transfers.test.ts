/* The main process's half of a transfer, and the one thing about it that is not obvious. */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { utilityProcess } from '../test/electron-stub';

import type { Arriving } from './transfer';
import { markerFiles } from './transfer';
import { type Child, TransferPool, transfers } from './transfers';
import type { TransferMessage, TransferRequest } from './transfer-worker';

let temp: string;
let at: Arriving;

beforeEach(() => {
	temp = fs.mkdtempSync(path.join(os.tmpdir(), 'sift-transfers-'));
	const finished = path.join(temp, 'clip.mp4');
	at = { partial: `${finished}.partial`, finished };
});

afterEach(() => {
	fs.rmSync(temp, { recursive: true, force: true });
});

/** A utility process, as a test can hold one. Nothing is forked. */
class FakeChild implements Child {
	readonly sent: TransferRequest[] = [];
	private messageListener: ((message: TransferMessage) => void) | null = null;
	private exitListener: (() => void) | null = null;

	postMessage(message: TransferRequest): void {
		this.sent.push(message);
	}

	on(event: 'message', listener: (message: TransferMessage) => void): void;
	on(event: 'exit', listener: () => void): void;
	on(event: 'message' | 'exit', listener: ((message: TransferMessage) => void) | (() => void)) {
		if (event === 'message') this.messageListener = listener as (m: TransferMessage) => void;
		else this.exitListener = listener as () => void;
	}

	say(message: TransferMessage): void {
		this.messageListener?.(message);
	}

	die(): void {
		this.exitListener?.();
	}
}

/* One second process for every drag, made on the first, and it is the compiled worker beside this
 * module: named any other way, the process fails to start only in the shipped build. */
describe('the pool', () => {
	it('is one, and forks the worker beside this module on the first transfer', async () => {
		const child = new FakeChild();
		const forked: [string, unknown][] = [];
		const fork = vi
			.spyOn(utilityProcess, 'fork')
			.mockImplementation(((file: string, _args: string[], options: unknown) => {
				forked.push([file, options]);
				return child;
			}) as never);
		try {
			expect(transfers()).toBe(transfers());
			const running = transfers().start('http://host/a', {}, at, 1, () => {});
			child.say({ id: child.sent[0].id, kind: 'settled', path: at.finished });

			expect(await running).toBe(at.finished);
			expect(forked).toEqual([
				[path.join(__dirname, 'transfer-worker.js'), { serviceName: 'sift-transfer' }]
			]);
		} finally {
			fork.mockRestore();
		}
	});
});

describe('a transfer', () => {
	it('asks the worker for exactly what it was given', async () => {
		const child = new FakeChild();
		const pool = new TransferPool(() => child);

		const running = pool.start('http://host/api/x', { Cookie: 'a=b' }, at, 12, () => {});
		child.say({ id: child.sent[0].id, kind: 'settled', path: at.finished });
		await running;

		expect(child.sent[0].url).toBe('http://host/api/x');
		expect(child.sent[0].headers).toEqual({ Cookie: 'a=b' });
		expect(child.sent[0].at).toEqual(at);
		expect(child.sent[0].total).toBe(12);
	});

	it('passes progress through as it arrives', async () => {
		const child = new FakeChild();
		const pool = new TransferPool(() => child);
		const reports: [number, number | null][] = [];

		const running = pool.start('http://host/a', {}, at, 12, (received, total) =>
			reports.push([received, total])
		);
		const { id } = child.sent[0];
		child.say({ id, kind: 'progress', received: 6, total: 12 });
		child.say({ id, kind: 'settled', path: at.finished });
		await running;

		expect(reports).toEqual([[6, 12]]);
	});

	/* One process, not one per drag. Starting a process costs tens of milliseconds and a drag is
	   meant to feel like a gesture. */
	it('reuses the one worker for a second transfer', async () => {
		let forks = 0;
		const child = new FakeChild();
		const pool = new TransferPool(() => {
			forks += 1;
			return child;
		});

		const first = pool.start('http://host/a', {}, at, null, () => {});
		const second = pool.start('http://host/b', {}, at, null, () => {});
		for (const sent of child.sent) child.say({ id: sent.id, kind: 'settled', path: null });
		await Promise.all([first, second]);

		expect(forks).toBe(1);
		expect(child.sent).toHaveLength(2);
	});

	/* Told apart by id, because they share a process and they do not finish in the order they
	   started: a small file behind a large one is done first. */
	it('settles the transfer the answer is about, not the one that started first', async () => {
		const child = new FakeChild();
		const pool = new TransferPool(() => child);
		const order: string[] = [];

		const first = pool.start('http://host/a', {}, at, null, () => {}).then(() => order.push('a'));
		const second = pool.start('http://host/b', {}, at, null, () => {}).then(() => order.push('b'));
		child.say({ id: child.sent[1].id, kind: 'settled', path: null });
		await second;
		child.say({ id: child.sent[0].id, kind: 'settled', path: null });
		await first;

		expect(order).toEqual(['b', 'a']);
	});
});

describe('a worker that dies', () => {
	it('leaves the failure marker for every transfer that was in flight', async () => {
		const child = new FakeChild();
		const pool = new TransferPool(() => child);
		fs.mkdirSync(path.dirname(at.partial), { recursive: true });

		const running = pool.start('http://host/a', {}, at, null, () => {});
		child.die();

		expect(await running).toBeNull();
		expect(fs.existsSync(markerFiles(at).failed)).toBe(true);
	});

	/* A transfer that reported its own conclusion has already left the right marker. */
	it('leaves nothing over a transfer that had already settled', async () => {
		const child = new FakeChild();
		const pool = new TransferPool(() => child);
		fs.mkdirSync(path.dirname(at.partial), { recursive: true });

		const running = pool.start('http://host/a', {}, at, null, () => {});
		child.say({ id: child.sent[0].id, kind: 'settled', path: at.finished });
		await running;
		child.die();

		expect(fs.existsSync(markerFiles(at).failed)).toBe(false);
	});

	it('starts a new worker for the next transfer', async () => {
		let forks = 0;
		const children: FakeChild[] = [];
		const pool = new TransferPool(() => {
			forks += 1;
			const made = new FakeChild();
			children.push(made);
			return made;
		});

		const first = pool.start('http://host/a', {}, at, null, () => {});
		children[0].die();
		await first;
		const second = pool.start('http://host/b', {}, at, null, () => {});
		children[1].say({ id: children[1].sent[0].id, kind: 'settled', path: null });
		await second;

		expect(forks).toBe(2);
	});

	/* A worker that cannot start AT ALL is the same answer as one that died: the caller is told
	   nothing came, and the marker is left so nothing is left waiting on it. */
	it('answers a fork that throws the same way', async () => {
		fs.mkdirSync(path.dirname(at.partial), { recursive: true });
		const pool = new TransferPool(() => {
			throw new Error('no process could be started');
		});

		expect(await pool.start('http://host/a', {}, at, null, () => {})).toBeNull();
		expect(fs.existsSync(markerFiles(at).failed)).toBe(true);
	});
});
