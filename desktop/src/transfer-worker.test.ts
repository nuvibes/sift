/* The utility process's entry point. It is four lines of wiring around one guarantee: whatever
 * happens, the main process is told the job has settled. */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Arriving } from './transfer';
import type { TransferMessage, TransferRequest } from './transfer-worker';

const transferTo = vi.fn();

vi.mock('./transfer', () => ({
	transferTo: (...args: unknown[]) => transferTo(...args)
}));

/** The channel back to the main process, as a test can hold one. */
class FakePort {
	readonly posted: TransferMessage[] = [];
	private listener: ((event: { data: unknown }) => void) | null = null;

	on(_event: 'message', listener: (event: { data: unknown }) => void): void {
		this.listener = listener;
	}

	postMessage(message: TransferMessage): void {
		this.posted.push(message);
	}

	deliver(job: TransferRequest): void {
		this.listener?.({ data: job });
	}
}

const at: Arriving = { partial: '/tmp/clip.mp4.partial', finished: '/tmp/clip.mp4' };

const job: TransferRequest = {
	id: 'job-1',
	url: 'http://127.0.0.1:5171/api/assets/abc/file',
	headers: { Cookie: 'session=x' },
	at,
	total: 2048
};

let port: FakePort;

/** Put a channel where the module looks for one, then import it fresh. */
async function start(): Promise<FakePort> {
	port = new FakePort();
	Object.defineProperty(process, 'parentPort', { value: port, configurable: true });
	vi.resetModules();
	await import('./transfer-worker');
	return port;
}

beforeEach(() => {
	transferTo.mockReset();
});

/** Wait for the job to report itself finished, however it finished. */
async function settled(): Promise<TransferMessage> {
	return vi.waitFor(() => {
		const done = port.posted.find((message) => message.kind === 'settled');
		expect(done).toBeDefined();
		return done as TransferMessage;
	});
}

describe('the transfer worker', () => {
	it('hands on what it was given and reports where the file landed', async () => {
		transferTo.mockResolvedValue('/tmp/clip.mp4');
		await start();

		port.deliver(job);

		expect(await settled()).toEqual({ id: 'job-1', kind: 'settled', path: '/tmp/clip.mp4' });
		/* Deliberately thin: no path is decided here and no address is built here. */
		const [, url, headers, arriving, total] = transferTo.mock.calls[0];
		expect(url).toBe(job.url);
		expect(headers).toBe(job.headers);
		expect(arriving).toBe(job.at);
		expect(total).toBe(job.total);
	});

	it('passes progress back as it arrives, under the id that was asked about', async () => {
		transferTo.mockImplementation(async (...args: unknown[]) => {
			const report = args[5] as (received: number, total: number | null) => void;
			report(512, 2048);
			report(2048, 2048);
			return '/tmp/clip.mp4';
		});
		await start();

		port.deliver(job);
		await settled();

		expect(port.posted.filter((message) => message.kind === 'progress')).toEqual([
			{ id: 'job-1', kind: 'progress', received: 512, total: 2048 },
			{ id: 'job-1', kind: 'progress', received: 2048, total: 2048 }
		]);
	});

	it('still reports the job settled when the transfer answers with nothing', async () => {
		transferTo.mockResolvedValue(null);
		await start();

		port.deliver(job);

		expect(await settled()).toEqual({ id: 'job-1', kind: 'settled', path: null });
	});

	it('still reports the job settled when the transfer throws', async () => {
		/* THE ONE THIS FILE EXISTS FOR. */
		transferTo.mockRejectedValue(new Error('the socket went away'));
		await start();

		port.deliver(job);

		expect(await settled()).toEqual({ id: 'job-1', kind: 'settled', path: null });
	});

	it('takes more than one job at a time', async () => {
		/* One job at a time is not assumed. Two arriving together must not be able to answer under
		 * each other's id, which is what a single shared variable in here would do. */
		transferTo.mockImplementation(async (...args: unknown[]) => String(args[1]));
		await start();

		const second: TransferRequest = { ...job, id: 'job-2', url: 'http://127.0.0.1:5171/second' };
		port.deliver(job);
		port.deliver(second);

		await vi.waitFor(() => {
			expect(port.posted.filter((message) => message.kind === 'settled')).toHaveLength(2);
		});
		expect(port.posted.filter((message) => message.kind === 'settled')).toEqual(
			expect.arrayContaining([
				{ id: 'job-1', kind: 'settled', path: job.url },
				{ id: 'job-2', kind: 'settled', path: second.url }
			])
		);
	});
});
