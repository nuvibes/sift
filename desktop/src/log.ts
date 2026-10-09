/* The shell's own log. */

import * as fs from 'node:fs';
import * as path from 'node:path';

import { shellLogFile } from './paths';

/** What a record may carry beyond the three fields every one of them has. */
export type Fields = Record<string, string | number | boolean | null | undefined>;

export type Level = 'debug' | 'info' | 'warning' | 'error';

/* Whether the detail is written: the `debug` records, which say how an act went as well as that
 * it happened. */
let detailed = /^debug$/i.test(process.env.SIFT_LOG_LEVEL ?? '');

/** Write the detail, or stop writing it: the Detail setting, as the page read it. */
export function setDetail(on: boolean): void {
	detailed = on;
}

/* Whether the account name in a home path is taken out as a record is written: the library's
 * `Hide personal details in the log`, once the page has said which (`setHidePersonal`). */
let hidePersonal = true;

/** Take names out of what is written, or write it whole: the setting, as the page read it. */
export function setHidePersonal(on: boolean): void {
	hidePersonal = on;
}

/** Whether names are being taken out. */
export function isHidingPersonal(): boolean {
	return hidePersonal;
}

/** Whether the detail is being written. */
export function isDetailed(): boolean {
	return detailed;
}

/* How big the file may get, and how much history is kept. */
const MOST_BYTES = 2 * 1024 * 1024;

export const REDACTED = '[redacted]';

/* The home-directory name, reduced out of any path. */
const HOME_SEGMENTS: readonly [RegExp, string][] = [
	[/(?<![\w/])(\/home\/)([^/\s"',;]+)/gi, '$1'],
	[/(?<![\w/])(\/Users\/)([^/\s"',;]+)/gi, '$1'],
	[/(?<![\w:\\])([A-Za-z]:\\\\Users\\\\)([^\\\s"',;]+)/gi, '$1'],
	[/(?<![\w:\\])([A-Za-z]:\\Users\\)([^\\\s"',;]+)/gi, '$1']
];

/* A password sitting in the authority part of a URL. */
const URL_USERINFO = /\b([a-z][a-z0-9+.-]{0,31}):\/\/([^/\s:@]+):([^/\s@]+)@/gi;

/* Keys whose VALUE is never written, whatever it is. */
const SECRET_KEY_PARTS = ['cookie', 'token', 'secret', 'password', 'passwd', 'auth', 'credential'];

function secretKey(key: string): boolean {
	const lowered = key.toLowerCase();
	return SECRET_KEY_PARTS.some((part) => lowered.includes(part));
}

/** A string with the credentials taken out, and the names while they are hidden. */
export function scrub(value: string): string {
	/* Three groups: bounding the scheme took the `://` out of the first one, and a two-group
	   replacement would write `httpssomeone:[redacted]@`. */
	let out = value.replace(URL_USERINFO, `$1://$2:${REDACTED}@`);
	if (!hidePersonal) return out;
	for (const [pattern, keep] of HOME_SEGMENTS) out = out.replace(pattern, `${keep}${REDACTED}`);
	return out;
}

/** One record's fields, scrubbed. Secret keys lose their value; every string is reduced. */
function clean(fields: Fields): Fields {
	const out: Fields = {};
	for (const [key, value] of Object.entries(fields)) {
		if (value === undefined) continue;
		if (secretKey(key)) {
			out[key] = REDACTED;
			continue;
		}
		out[key] = typeof value === 'string' ? scrub(value) : value;
	}
	return out;
}

/** The record, as the line that will be written. */
export function record(level: Level, event: string, fields: Fields = {}): string {
	return `${JSON.stringify({
		...clean(fields),
		event: scrub(event),
		level,
		timestamp: new Date().toISOString()
	})}\n`;
}

/* How many bytes are in the file, carried rather than asked for. */
let written: number | null = null;

/** Where it goes, remembered so a test can point it somewhere else and put it back. */
let file: string | null = null;

function target(): string {
	if (file === null) file = shellLogFile();
	return file;
}

/** Point the log at a different file. FOR TESTS, and it resets the size with it. */
export function logTo(where: string | null): void {
	file = where;
	written = null;
}

/* Roll the file over when it has had enough. One generation, renamed rather than copied, so the
 * swap is atomic and cannot leave a half file. */
function rollIfFull(at: string, adding: number): void {
	if (written === null) {
		try {
			written = fs.statSync(at).size;
		} catch {
			written = 0;
		}
	}
	if (written + adding <= MOST_BYTES) return;
	const previous = `${at}.1`;
	try {
		fs.rmSync(previous, { force: true });
		fs.renameSync(at, previous);
	} catch {
		/* Could not roll: keep writing to the file rather than stopping. */
	}
	written = 0;
}

function write(level: Level, event: string, fields: Fields): void {
	try {
		const line = record(level, event, fields);
		const at = target();
		const bytes = Buffer.byteLength(line);
		rollIfFull(at, bytes);
		fs.mkdirSync(path.dirname(at), { recursive: true });
		/* Synchronous, and that is the right answer HERE specifically. */
		fs.appendFileSync(at, line);
		written = (written ?? 0) + bytes;
	} catch {
		/* Deliberately silent and deliberately total. */
	}
}

export const log = {
	/** Detail: written only while the Detail setting says so (see `setDetail`). */
	debug: (event: string, fields: Fields = {}) => {
		if (detailed) write('debug', event, fields);
	},
	info: (event: string, fields: Fields = {}) => write('info', event, fields),
	warning: (event: string, fields: Fields = {}) => write('warning', event, fields),
	error: (event: string, fields: Fields = {}) => write('error', event, fields),
	/** Where the file is, for the screen that offers to show it. */
	where: (): string => target()
};

/** How many lines an ask for the end of the log gets: a count held to 1 to 2000, and 200 when what
 *  arrived is not a count. The page's verb and the backend's link ask by the same rule. */
export function linesAsked(asked: unknown): number {
	const wanted =
		typeof asked === 'number'
			? asked
			: typeof asked === 'string' && asked.trim() !== ''
				? Number(asked)
				: Number.NaN;
	if (!Number.isFinite(wanted)) return 200;
	return Math.min(Math.max(Math.trunc(wanted), 1), 2000);
}

/** The end of the log, oldest first, as the page asks for it. */
export function tail(lines: number): {
	lines: string[];
	path: string;
	size: number;
	present: boolean;
} {
	const at = target();
	try {
		const size = fs.statSync(at).size;
		const text = fs.readFileSync(at, 'utf8');
		const all = text.split('\n').filter((one) => one.trim() !== '');
		return { lines: all.slice(-lines), path: at, size, present: true };
	} catch {
		return { lines: [], path: at, size: 0, present: false };
	}
}
