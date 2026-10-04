/* The utility process that does the fetching. One job at a time is not assumed; several may run.
 *
 * This exists for one reason, and it is written at the top of transfer.ts: the main process's event
 * loop is blocked while a drop is being taken, so it cannot also be the thing producing the bytes
 * the receiver is waiting for. Everything here runs somewhere that cannot be blocked by a drag.
 *
 * It is deliberately thin. No paths are decided here and no addresses are built here. Both arrive
 * fully formed from the main process, which is the side that knows where the temporary folder is
 * and which server the asking window belongs to. A worker that worked either of those out for
 * itself would be a second place for them to be wrong.
 */

import { type Arriving, transferTo } from './transfer';

/** What the main process asks for. */
export interface TransferRequest {
	id: string;
	url: string;
	headers: Record<string, string>;
	at: Arriving;
	total: number | null;
}

/** What it is told back. */
export type TransferMessage =
	| { id: string; kind: 'progress'; received: number; total: number | null }
	| { id: string; kind: 'settled'; path: string | null };

const port = process.parentPort;

port.on('message', (event) => {
	const job = event.data as TransferRequest;
	void run(job);
});

async function run(job: TransferRequest): Promise<void> {
	const say = (message: TransferMessage): void => port.postMessage(message);
	let path: string | null = null;
	try {
		path = await transferTo(
			(url, init) => fetch(url, init),
			job.url,
			job.headers,
			job.at,
			job.total,
			(received, total) => say({ id: job.id, kind: 'progress', received, total })
		);
	} catch {
		/* `transferTo` answers null for everything it expects to go wrong and leaves the failure
		 * marker itself. Reaching here means something it did not expect, and the one thing that
		 * must still happen is that the main process is told, so it can leave the marker and the
		 * receiver stops waiting. An unhandled rejection here would take this process down instead,
		 * which reads to a receiver as a transfer that simply stopped. */
	}
	say({ id: job.id, kind: 'settled', path });
}
