/* The Smart Search status line: described, not described, and the files not read yet. */

import { expect, it } from 'vitest';
import { semanticStatusLine } from './Semantic.search';

type Status = NonNullable<Parameters<typeof semanticStatusLine>[0]>;

function status(over: Partial<Status> = {}): Status {
	return {
		supported: true,
		enabled: true,
		ready: true,
		family: 'base',
		device: 'cpu',
		indexed_frames: 0,
		described_files: 0,
		waiting_files: 0,
		unread_files: 0,
		running_jobs: 0,
		problem: null,
		described_by_another_model: 0,
		installed: [],
		...over
	};
}

it('says the files not read yet after the ones not described', () => {
	const line = semanticStatusLine(
		status({ described_files: 42423, waiting_files: 4, unread_files: 51793 }),
		true,
		'GPU'
	);
	expect(line).toBe(
		`Ready. Running on the GPU. ${(42423).toLocaleString()} files described, 4 not described yet, and ${(51793).toLocaleString()} more waiting to be scanned.`
	);
});

it('says what it said before when nothing is unread', () => {
	expect(semanticStatusLine(status({ described_files: 1, waiting_files: 0 }), true, 'CPU')).toBe(
		'Ready. Running on the CPU. 1 file described, 0 not described yet.'
	);
});
