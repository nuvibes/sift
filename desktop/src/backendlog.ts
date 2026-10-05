/* The backend's output, one file per start: `backend.log` is the start running now and
 * `backend.log.1` to `.3` the ones before it, each at most MOST_BYTES. */

import { app } from 'electron';
import * as fs from 'node:fs';
import * as path from 'node:path';

import { record } from './log';
import { backendLogFile } from './paths';

export const MOST_BYTES = 2 * 1024 * 1024;
export const KEEP = 4;

const START = /"start":(\d+)/;

/** The number on the first line of the file, which is the start that wrote it; 0 for none. */
export function lastStart(at: string): number {
	try {
		const head = Buffer.alloc(512);
		const fd = fs.openSync(at, 'r');
		const read = fs.readSync(fd, head, 0, head.length, 0);
		fs.closeSync(fd);
		const found = START.exec(head.subarray(0, read).toString('utf8').split('\n')[0] ?? '');
		return found === null ? 0 : Number(found[1]);
	} catch {
		return 0;
	}
}

/** Every file one place older and the oldest gone; a file over the cap keeps only its end. */
export function roll(at: string): void {
	for (let older = KEEP - 2; older >= 1; older -= 1) {
		if (fs.existsSync(`${at}.${older}`)) fs.renameSync(`${at}.${older}`, `${at}.${older + 1}`);
	}
	if (!fs.existsSync(at)) return;
	const size = fs.statSync(at).size;
	if (size <= MOST_BYTES) {
		fs.renameSync(at, `${at}.1`);
		return;
	}
	const end = Buffer.alloc(MOST_BYTES);
	const fd = fs.openSync(at, 'r');
	fs.readSync(fd, end, 0, MOST_BYTES, size - MOST_BYTES);
	fs.closeSync(fd);
	fs.writeFileSync(`${at}.1`, end.subarray(end.indexOf(0x0a) + 1));
	fs.rmSync(at, { force: true });
}

/* The start writing now. One that has ended keeps its file open for output still in the pipe, and
 * stops at the cap rather than rolling a file that is now another start's. */
let newest: StartLog | null = null;

export class StartLog {
	private fd: number | null = null;
	private written = 0;

	constructor(
		private readonly at: string,
		readonly start: number,
		private readonly version: string
	) {
		newest = this;
		this.begin('backend.start');
	}

	private begin(event: string): void {
		try {
			fs.mkdirSync(path.dirname(this.at), { recursive: true });
			roll(this.at);
		} catch {
			/* Not rolled: appended to, rather than lost. */
		}
		try {
			this.fd = fs.openSync(this.at, 'a');
			this.written = fs.fstatSync(this.fd).size;
		} catch {
			this.fd = null;
		}
		this.put(Buffer.from(record('info', event, { version: this.version, start: this.start })));
	}

	write(chunk: Buffer): void {
		if (this.fd !== null && this.written + chunk.length > MOST_BYTES) {
			if (newest !== this) return;
			this.close();
			this.begin('backend.start_continued');
		}
		this.put(chunk);
	}

	private put(chunk: Buffer): void {
		if (this.fd === null) return;
		try {
			fs.writeSync(this.fd, chunk);
			this.written += chunk.length;
		} catch {
			/* A full disk is no reason for the backend to stop. */
		}
	}

	close(): void {
		if (this.fd === null) return;
		try {
			fs.closeSync(this.fd);
		} catch {
			/* already closed */
		}
		this.fd = null;
	}
}

/** Begin a start's file, numbered one past the start before it. */
export function openStart(
	at: string = backendLogFile(),
	version: string = app.getVersion()
): StartLog {
	return new StartLog(at, lastStart(at) + 1, version);
}
