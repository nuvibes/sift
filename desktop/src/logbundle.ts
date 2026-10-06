/* Download log from the crash dialog: the bundled Python's one-shot scrubber, which needs no
 * backend and loads no native library, writes every log redacted into one archive. */

import { ipcMain } from 'electron';
import { spawn as spawnReal } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { INTERPRETER_ARGS } from './backend';
import { SAVE_LOG_ARCHIVE } from './channels';
import { takeGesture } from './gesture';
import { log, type Fields } from './log';
import { askingFrame, type ReachCheck } from './verbs';

/** Long enough for a few megabytes of logs; a scrubber that hangs is ended and said. */
export const BUNDLE_TIMEOUT_MS = 60_000;

export type Bundled = { ok: true; file: string } | { ok: false; reason: string };

/** What the page is told: the file's path, or why there is none. */
export type LogArchiveOutcome = { file: string } | { reason: string };

export interface BundleAsk {
	python: string;
	appLogs: string;
	libraryLogs: string | null;
	facts: Fields;
	into: string;
	/** The archive's file name; the time it was made where none is given. */
	name?: string;
	now?: Date;
	spawn?: typeof spawnReal;
}

/** The archive's name, as the page names it: this device's own time, so a second press never
 * overwrites the first. */
export function archiveName(now: Date): string {
	const two = (part: number): string => String(part).padStart(2, '0');
	const day = `${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())}`;
	return `sift-log ${day} ${two(now.getHours())}-${two(now.getMinutes())}-${two(now.getSeconds())}.zip`;
}

export function bundleLogs(ask: BundleAsk): Promise<Bundled> {
	const out = path.join(ask.into, ask.name ?? archiveName(ask.now ?? new Date()));
	const facts = path.join(os.tmpdir(), `sift-facts-${process.pid}-${Date.now()}.txt`);
	try {
		fs.writeFileSync(
			facts,
			Object.entries(ask.facts)
				.map(([name, value]) => `${name}: ${String(value)}\n`)
				.join('')
		);
	} catch (err) {
		return Promise.resolve({
			ok: false,
			reason: err instanceof Error ? err.message : String(err)
		});
	}
	const args = [
		...INTERPRETER_ARGS,
		'-m',
		'sift.logbundle',
		'--out',
		out,
		'--app-logs',
		ask.appLogs,
		...(ask.libraryLogs === null ? [] : ['--library-logs', ask.libraryLogs]),
		'--facts',
		facts
	];
	return new Promise((resolve) => {
		let said = '';
		let finished = false;
		const done = (result: Bundled): void => {
			if (finished) return;
			finished = true;
			clearTimeout(timer);
			fs.rmSync(facts, { force: true });
			resolve(result);
		};
		const child = (ask.spawn ?? spawnReal)(ask.python, args, {
			stdio: ['ignore', 'ignore', 'pipe'],
			windowsHide: true
		});
		const timer = setTimeout(() => {
			child.kill();
			done({ ok: false, reason: 'Making the log file took too long.' });
		}, BUNDLE_TIMEOUT_MS);
		child.stderr?.on('data', (chunk: Buffer) => {
			said += chunk.toString('utf8');
		});
		child.once('error', (err) => done({ ok: false, reason: err.message }));
		child.once('close', (code) => {
			if (code === 0 && fs.existsSync(out)) return done({ ok: true, file: out });
			const line = said.trim().split('\n')[0]?.trim();
			done({
				ok: false,
				reason: line || `The log maker stopped with exit code ${String(code)}.`
			});
		});
	});
}

/** The page's Download log. A page from another computer only just after a press, as a capture is;
 *  only the name's last part is taken, so the archive lands in the save folder whatever was sent. */
export function registerLogArchive(
	reachOf: ReachCheck,
	archive: (name: string) => Promise<Bundled>
): void {
	ipcMain.handle(SAVE_LOG_ARCHIVE, async (event, name: unknown): Promise<LogArchiveOutcome> => {
		const refused = (why: string): LogArchiveOutcome => {
			log.error('shell.log_download_refused', { why });
			return { reason: why };
		};
		const frame = askingFrame(event, reachOf, SAVE_LOG_ARCHIVE);
		if (frame === null) return refused('the page is not one this shell serves');
		if (typeof name !== 'string') return refused('no file name');
		if (reachOf(frame.url) === 'remote' && !takeGesture(event.sender)) return refused('no press');
		const base = path.win32.basename(name);
		if (base === '' || base === '.' || base === '..') return refused('an empty file name');
		try {
			const made = await archive(base);
			return made.ok ? { file: made.file } : { reason: made.reason };
		} catch (err) {
			const reason = err instanceof Error ? err.message : String(err);
			log.error('shell.log_download_failed', { reason });
			return { reason };
		}
	});
}
