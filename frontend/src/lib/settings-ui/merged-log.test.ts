import { describe, expect, it } from 'vitest';
import { mergeLogs, type LogRead } from './merged-log';

function read(lines: [string | null, string][], cut = false): LogRead {
	return {
		cut,
		lines: lines.map(([at, event]) => ({ at, event, level: 'info', raw: event }))
	};
}

function order(library: LogRead, app: LogRead | null): string[] {
	return mergeLogs(library, app).map(({ line, from }) => `${from}:${line.event}`);
}

describe('the one list of both logs', () => {
	it('is in the order the lines were written, whichever log each is from', () => {
		const library = read([
			['2026-10-03T06:00:00Z', 'scan.started'],
			['2026-10-03T06:00:05Z', 'scan.done']
		]);
		const app = read([
			['2026-10-03T06:00:02Z', 'drag.started'],
			['2026-10-03T06:00:09Z', 'update.checked']
		]);
		expect(order(library, app)).toEqual([
			'library:scan.started',
			'app:drag.started',
			'library:scan.done',
			'app:update.checked'
		]);
	});

	it('keeps a line with no time after the line it followed in its own log', () => {
		const library = read([
			['2026-10-03T06:00:00Z', 'job.failed'],
			[null, 'Traceback'],
			['2026-10-03T06:00:05Z', 'job.retried']
		]);
		const app = read([['2026-10-03T06:00:03Z', 'drag.started']]);
		expect(order(library, app)).toEqual([
			'library:job.failed',
			'library:Traceback',
			'app:drag.started',
			'library:job.retried'
		]);
	});

	it('puts the library first where two lines share a moment', () => {
		const library = read([['2026-10-03T06:00:00Z', 'one']]);
		const app = read([['2026-10-03T06:00:00Z', 'two']]);
		expect(order(library, app)).toEqual(['library:one', 'app:two']);
	});

	it('keeps to the span a cut read can vouch for', () => {
		const library = read(
			[
				['2026-10-03T06:00:00Z', 'newest.run'],
				['2026-10-03T06:00:05Z', 'newest.end']
			],
			true
		);
		const app = read([
			['2026-09-28T06:00:00Z', 'last.week'],
			['2026-10-03T06:00:03Z', 'drag.started']
		]);
		expect(order(library, app)).toEqual([
			'library:newest.run',
			'app:drag.started',
			'library:newest.end'
		]);
	});

	it("keeps every line of a job's cut read, its summary first", () => {
		const library = read(
			[
				['2026-10-03T06:00:09Z', 'job.summary'],
				['2026-10-03T06:00:00Z', 'job.claimed'],
				['2026-10-03T06:00:05Z', 'content.probed']
			],
			true
		);
		expect(order(library, read([]))).toEqual([
			'library:job.summary',
			'library:job.claimed',
			'library:content.probed'
		]);
	});

	it('is the library alone where there is no app log', () => {
		const library = read([['2026-10-03T06:00:00Z', 'scan.started']], true);
		expect(order(library, null)).toEqual(['library:scan.started']);
	});
});
