// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Logs page's one list: the library's lines and the app's own, in the order they were
 * written. */
import type { components } from '$lib/api/schema';

type LogRecord = components['schemas']['LogLine'];

export type LogSource = 'library' | 'app';

export interface MarkedLine {
	line: LogRecord;
	from: LogSource;
}

export interface LogRead {
	lines: readonly LogRecord[];
	cut: boolean;
}

/** When a line was written, in unix seconds, or null where it carries no readable stamp. */
export function momentOf(line: LogRecord): number | null {
	if (!line.at) return null;
	const moment = Date.parse(line.at);
	return Number.isNaN(moment) ? null : moment / 1000;
}

function carried(lines: readonly LogRecord[]): number[] {
	let last = -Infinity;
	return lines.map((line) => {
		const own = momentOf(line);
		if (own !== null) last = own;
		return last;
	});
}

function floorOf(read: LogRead | null): number {
	if (!read || !read.cut) return -Infinity;
	const known = read.lines.map(momentOf).find((one) => one !== null);
	return known ?? -Infinity;
}

/** The two logs as one list, in the order the lines were written. `app` is null where there is none. */
export function mergeLogs(library: LogRead, app: LogRead | null): MarkedLine[] {
	const floor = Math.max(floorOf(library), floorOf(app));
	const ours = carried(library.lines);
	const theirs = app ? carried(app.lines) : [];
	const appLines = app?.lines ?? [];
	const merged: MarkedLine[] = [];
	let a = 0;
	let b = 0;
	while (a < library.lines.length || b < appLines.length) {
		const takeLibrary = b >= appLines.length || (a < library.lines.length && ours[a] <= theirs[b]);
		if (takeLibrary) {
			if (ours[a] >= floor) merged.push({ line: library.lines[a], from: 'library' });
			a += 1;
		} else {
			if (theirs[b] >= floor) merged.push({ line: appLines[b], from: 'app' });
			b += 1;
		}
	}
	return merged;
}
