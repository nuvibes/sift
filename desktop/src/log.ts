/*
 * The shell's own log.
 *
 * ## Why this exists
 *
 * The two files that look like logs are neither: `backend.log` is a capture of the Python process's
 * stdout, which does not exist in client mode, and `sift.log` belongs to the server, which in
 * client mode is a different computer. Everything only the shell knows (which path a drag took,
 * whether a share answered inside its budget, whether the native addon loaded, what an update check
 * said, why a transfer died) is recorded here.
 *
 * ## The shape is the backend's shape
 *
 * One JSON object per line: the fields, then `event`, `level`, `timestamp`. That is what structlog
 * writes on the server, so `slices/logs`'s parser reads these records and the Application log
 * screen draws them with no change.
 *
 * ## Redaction is at source, and the rule is the server's rule
 *
 * `kernel/log.py` sets it out. Credentials are never written. The account name after a home root
 * becomes `[redacted]` while the library's `Hide personal details in the log` is on (`setHidePersonal`);
 * a path is reduced, not erased, because the mount point, the folder layout and the filename are
 * what tell you what went wrong, and none of them names anybody.
 *
 * ## It cannot throw
 *
 * Every call is wrapped. A full disk, a folder taken away, a file another process holds open: none
 * of them is a reason for a drag to stop working.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';

import { shellLogFile } from './paths';

/** What a record may carry beyond the three fields every one of them has. */
export type Fields = Record<string, string | number | boolean | null | undefined>;

export type Level = 'debug' | 'info' | 'warning' | 'error';

/*
 * Whether the detail is written: the `debug` records, which say how an act went as well as that it
 * happened. The library's own choice (`Settings > Activity > Log`, the Detail setting) decides it
 * once the page has said which (`setDetail`); until then, the level the backend is started with
 * (`SIFT_LOG_LEVEL`), which is the one answer there is before a library is open. Everything else
 * (what happened, and anything that went wrong) is written whatever this says.
 */
let detailed = /^debug$/i.test(process.env.SIFT_LOG_LEVEL ?? '');

/** Write the detail, or stop writing it: the Detail setting, as the page read it. */
export function setDetail(on: boolean): void {
	detailed = on;
}

/*
 * Whether the account name in a home path is taken out as a record is written: the library's
 * `Hide personal details in the log`, once the page has said which (`setHidePersonal`). Until then
 * it is hidden, the server's rule for the lines it writes before the library is open: somebody who
 * chose to hide names is never written whole for want of an answer.
 */
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

/*
 * How big the file may get, and how much history is kept.
 *
 * Two megabytes is thousands of records at this volume: the shell logs EVENTS, not requests, so
 * a busy day is dozens of lines rather than the tens of thousands the server writes. One previous
 * generation is kept, which is the difference between "what happened just now" and "what happened
 * before the restart that lost it".
 */
const MOST_BYTES = 2 * 1024 * 1024;

export const REDACTED = '[redacted]';

/*
 * The home-directory name, reduced out of any path.
 *
 * The same rule and the same replacement as the server's, from `kernel/log.py`: the segment after
 * the home root is the only identifying part, so it is the only part that goes. The lookbehind is
 * what stops it matching in the middle of something else: a URL path, a word ending in `users`.
 *
 * Both separators for the Windows form, because a path that has been through `JSON.stringify` once
 * arrives with its backslashes doubled, and the single-slash pattern does not match it.
 */
const HOME_SEGMENTS: readonly [RegExp, string][] = [
	[/(?<![\w/])(\/home\/)([^/\s"',;]+)/gi, '$1'],
	[/(?<![\w/])(\/Users\/)([^/\s"',;]+)/gi, '$1'],
	[/(?<![\w:\\])([A-Za-z]:\\\\Users\\\\)([^\\\s"',;]+)/gi, '$1'],
	[/(?<![\w:\\])([A-Za-z]:\\Users\\)([^\\\s"',;]+)/gi, '$1']
];

/*
 * A password sitting in the authority part of a URL. The shell holds server addresses somebody
 * typed, and `https://someone:hunter2@box:5171` is one a person can legitimately save.
 *
 * The scheme is anchored and bounded because an unbounded `(\w+:\/\/)` is quadratic on any long run
 * of word characters: `\w+` eats the whole run at every starting position, fails to find `://`, and
 * backs off one character at a time. In a synchronous call on the main thread that freezes the
 * window.
 *
 * `\b` means the scheme is only tried where a word starts, and `{0,31}` means it can never eat more
 * than a scheme's worth. Same matches, linear cost.
 */
const URL_USERINFO = /\b([a-z][a-z0-9+.-]{0,31}):\/\/([^/\s:@]+):([^/\s@]+)@/gi;

/* Keys whose VALUE is never written, whatever it is. The server's list, cut to what this side can
   plausibly hold. Matched as a substring of the key, lowercased, so `authToken` and `auth_token`
   are the same answer. */
const SECRET_KEY_PARTS = ['cookie', 'token', 'secret', 'password', 'passwd', 'auth', 'credential'];

function secretKey(key: string): boolean {
	const lowered = key.toLowerCase();
	return SECRET_KEY_PARTS.some((part) => lowered.includes(part));
}

/** A string with the credentials taken out, and the names while they are hidden. */
export function scrub(value: string): string {
	/* Three groups: bounding the scheme took the `://` out of the first one, and a two-group
	   replacement would write `httpssomeone:[redacted]@`. The test that reads the whole address
	   back guards it. */
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

/**
 * The record, as the line that will be written.
 *
 * Separated from the writing so the shape can be asserted without a filesystem, and so the ORDER is
 * stated in one place: the fields first and the three fixed ones last, matching the server exactly.
 */
export function record(level: Level, event: string, fields: Fields = {}): string {
	return `${JSON.stringify({
		...clean(fields),
		event: scrub(event),
		level,
		timestamp: new Date().toISOString()
	})}\n`;
}

/* How many bytes are in the file, carried rather than asked for.
 *
 * A `statSync` per record would be a syscall per line for a number this module already knows: it
 * writes every byte that goes in. Null means "not looked yet", which is the first write of a run,
 * and looking then is what makes an existing file from a previous run roll over at the right size
 * rather than growing by another two megabytes first.
 */
let written: number | null = null;

/** Where it goes, remembered so a test can point it somewhere else and put it back. */
let file: string | null = null;

function target(): string {
	if (file === null) file = shellLogFile();
	return file;
}

/**
 * Point the log at a different file. FOR TESTS, and it resets the size with it.
 *
 * Not a setting: where the log lives is decided by `paths.ts` for the same reason every other
 * location is, and a shell that could be told to write anywhere is a shell that can be told to
 * write over something.
 */
export function logTo(where: string | null): void {
	file = where;
	written = null;
}

/* Roll the file over when it has had enough.
 *
 * One generation, renamed rather than copied, so the swap is atomic and cannot leave a half file.
 * `.1` is dropped first because a rename onto an existing name is an error on Windows. POSIX
 * would have replaced it silently, which is exactly the kind of difference that ships. */
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
		/* Could not roll: keep writing to the file rather than stopping. An oversized log is a
		   nuisance; a log that goes silent the day it fills is the fault this whole file exists
		   to end. */
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
		/* Synchronous, and that is the right answer HERE specifically. The file is in the shell's own
		   settings folder, which is always local storage (never the library, never a share), so a
		   write is microseconds. What it buys is that the last record before a crash is on disk,
		   which is the record that matters. */
		fs.appendFileSync(at, line);
		written = (written ?? 0) + bytes;
	} catch {
		/* Deliberately silent and deliberately total. There is nowhere to report a logging failure
		   TO (reporting it is the thing that just failed), and no failure in here may become a
		   failure of whatever was being logged. */
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

/**
 * The end of the log, oldest first, as the page asks for it.
 *
 * Whole-file rather than a seek from the end, and that is a deliberate trade this side can afford:
 * the cap above is two megabytes, which is nothing to read, and the alternative is the backend's
 * chunked tail: a hundred lines of code to save a few milliseconds on a file somebody opens when
 * something has gone wrong. The server needs it because its log is unbounded in practice.
 */
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
