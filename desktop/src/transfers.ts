/* The main process's half of a transfer: starting one, following it, and noticing when it dies.
 *
 * WHY THERE IS A SECOND PROCESS AT ALL is written at the top of transfer.ts and is the whole
 * reason this file exists: a drop being taken blocks the main thread inside a stream read, so the
 * bytes that read is waiting for cannot also be produced by the main thread.
 *
 * ONE PROCESS, NOT ONE PER TRANSFER. Starting a process costs tens of milliseconds and a drag is
 * meant to feel like a gesture, not like launching something. Several transfers share it; each is
 * told apart by an id, and the process is long-lived because there is nothing in it worth tearing
 * down between drags.
 *
 * THE FAILURE THAT MATTERS IS THE PROCESS DYING. A transfer that fails reports it and leaves the
 * marker file a receiver watches for. A transfer whose PROCESS has gone cannot do either, so the
 * marker is left here, from the outside, for every job that was in flight. Without that, a receiver
 * sits on a file that has stopped growing until its patience runs out and then writes out whatever
 * arrived.
 */

import { utilityProcess } from 'electron';
import * as path from 'node:path';

import { type Arriving, markFailed, type Progress } from './transfer';
import type { TransferMessage, TransferRequest } from './transfer-worker';

/** The little of a utility process this needs, so a test can be one. */
export interface Child {
	postMessage(message: TransferRequest): void;
	on(event: 'message', listener: (message: TransferMessage) => void): void;
	on(event: 'exit', listener: () => void): void;
}

/** How a child is made. Injected so a test never forks anything. */
export type Fork = () => Child;

const forkWorker: Fork = () => {
	/* `__dirname`, so this is the compiled worker beside the compiled main process: inside the
	 * asar in an installed copy, and in `dist/` from a checkout. Naming it any other way is how a
	 * second process quietly fails to start only in the shipped build. */
	const child = utilityProcess.fork(path.join(__dirname, 'transfer-worker.js'), [], {
		serviceName: 'sift-transfer'
	});
	return child as unknown as Child;
};

interface Waiting {
	at: Arriving;
	onProgress: Progress;
	settle: (path: string | null) => void;
}

export class TransferPool {
	private child: Child | null = null;
	private readonly waiting = new Map<string, Waiting>();
	private next = 0;

	constructor(private readonly fork: Fork = forkWorker) {}

	/** Fetch to `at.partial` and rename to `at.finished`. The finished path, or null. */
	start(
		url: string,
		headers: Record<string, string>,
		at: Arriving,
		total: number | null,
		onProgress: Progress
	): Promise<string | null> {
		const id = String(++this.next);
		return new Promise((settle) => {
			this.waiting.set(id, { at, onProgress, settle });
			try {
				this.running().postMessage({ id, url, headers, at, total });
			} catch {
				/* A process that would not start at all. Answered the same way as one that died: the
				 * marker is left so a receiver does not wait, and the caller is told nothing came. */
				this.finish(id, null, true);
			}
		});
	}

	private running(): Child {
		if (this.child !== null) return this.child;
		const child = this.fork();
		this.child = child;
		child.on('message', (message: TransferMessage) => this.heard(message));
		child.on('exit', () => {
			this.child = null;
			/* Everything that was in flight is now a transfer nobody will ever finish. Each one gets
			 * its failure marker, because the receiver on the other side of it has no other way to
			 * learn the difference between a writer that is slow and a writer that is gone. */
			for (const id of [...this.waiting.keys()]) this.finish(id, null, true);
		});
		return child;
	}

	private heard(message: TransferMessage): void {
		const job = this.waiting.get(message.id);
		if (job === undefined) return;
		if (message.kind === 'progress') {
			job.onProgress(message.received, message.total);
			return;
		}
		/* No marker here: a transfer that ran to a conclusion has already left its own, and the one
		 * it left says which conclusion it was. */
		this.finish(message.id, message.path, false);
	}

	private finish(id: string, result: string | null, died: boolean): void {
		const job = this.waiting.get(id);
		if (job === undefined) return;
		this.waiting.delete(id);
		if (died) markFailed(job.at);
		job.settle(result);
	}
}

let pool: TransferPool | null = null;

/** The one pool. Made on first use, because nothing before a drag needs a second process. */
export function transfers(): TransferPool {
	if (pool === null) pool = new TransferPool();
	return pool;
}
