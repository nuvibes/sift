/* The utility process that does the fetching. One job at a time is not assumed; several may run. */

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
		 * marker itself. */
	}
	say({ id: job.id, kind: 'settled', path });
}
