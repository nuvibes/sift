import { describe, expect, it, vi } from 'vitest';
import { WatchReport, type WatchPiece, type Watched } from './watch-report';

/* A player whose post answers at once, or, once `hold()` is called, only when `land()` says, so
   a reply can land after the next file began. */
function player() {
	const pieces: WatchPiece[] = [];
	let held = false;
	let release: () => void = () => {};
	const watched: Watched = {
		position: () => 0,
		duration: () => 100,
		rate: () => 1,
		playhead: () => 0,
		viewAt: () => 0,
		post: (piece) => {
			pieces.push(piece);
			if (!held) return Promise.resolve();
			held = false;
			return new Promise<void>((resolve) => {
				release = resolve;
			});
		}
	};
	return {
		watched,
		pieces,
		hold: () => {
			held = true;
		},
		land: () => release()
	};
}

/* Count `ms` of watching on the report, by the clock the tick reads. */
function watch(report: WatchReport, ms: number) {
	const now = vi.spyOn(performance, 'now');
	now.mockReturnValue(1000);
	report.begin();
	now.mockReturnValue(1000 + ms);
	report.accumulate();
	now.mockRestore();
}

describe('a reply that lands after the next file began', () => {
	it('leaves the next sitting with nothing sent and its own heat', async () => {
		const { watched, pieces, hold, land } = player();
		const report = new WatchReport(watched, 10);
		watch(report, 5000);
		hold();
		const closing = report.closePass();
		expect(pieces[0]?.watch_ms).toBe(5000);

		// The next file: a new sitting, counted on its own before the first reply lands.
		report.restart();
		watch(report, 2000);
		land();
		await closing;

		await report.report();
		const next = pieces[1];
		expect(next?.sitting).not.toBe(pieces[0]?.sitting);
		// The whole 2,000 ms, not 2,000 less the last file's 5,000; nothing already reported.
		expect(next?.watch_ms).toBe(2000);
		expect(next?.already_reported_ms).toBeNull();
		expect(next?.heat).toEqual({ '0': 2000 });
	});

	it('still takes what landed off the sitting it was counted for', async () => {
		const { watched, pieces, hold, land } = player();
		const report = new WatchReport(watched, 10);
		watch(report, 5000);
		hold();
		const closing = report.closePass();
		land();
		await closing;
		watch(report, 1000);
		await report.report();
		expect(pieces[1]?.watch_ms).toBe(1000);
		expect(pieces[1]?.already_reported_ms).toBeNull();
	});
});
