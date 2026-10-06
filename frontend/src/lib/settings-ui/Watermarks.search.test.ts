/* The watermark status line: the page still to read, and the files not read yet. */

import { expect, it } from 'vitest';
import { watermarksStatusLine } from './Watermarks.search';

type Status = NonNullable<Parameters<typeof watermarksStatusLine>[0]>;

function status(over: Partial<Status> = {}): Status {
	return {
		enabled: true,
		ready: true,
		device: 'cpu',
		read_files: 10,
		marks_found: 2,
		waiting_files: 0,
		unread_files: 0,
		running_jobs: 0,
		problem: null,
		installed: [],
		...over
	};
}

const READY = 'Ready. Running on the CPU. 10 files scanned, 2 with a watermark.';

it('says the files not read yet after the page still to read', () => {
	expect(watermarksStatusLine(status({ waiting_files: 500, unread_files: 2 }), true, 'CPU')).toBe(
		`${READY} At least 500 not scanned yet, and 2 more waiting to be scanned.`
	);
});

it('never says every file is scanned while files are not read yet', () => {
	expect(watermarksStatusLine(status({ unread_files: 1 }), true, 'CPU')).toBe(
		`${READY} 1 file waiting to be scanned.`
	);
});

it('says what it said before when nothing is unread', () => {
	expect(watermarksStatusLine(status({ waiting_files: 3 }), true, 'CPU')).toBe(
		`${READY} At least 3 not scanned yet.`
	);
	expect(watermarksStatusLine(status(), true, 'CPU')).toBe(`${READY} Every file is scanned.`);
});
