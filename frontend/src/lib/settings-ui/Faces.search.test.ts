/* The Faces status line: the read files left, then the files not read yet, in one sentence. */

import { expect, it } from 'vitest';
import { facesStatus } from './Faces.search';

type Feature = NonNullable<Parameters<typeof facesStatus>[0]>;

function feature(over: Partial<Feature> = {}): Feature {
	return {
		enabled: true,
		ready: true,
		family: 'accurate',
		device: 'nvidia',
		depth: 'fast',
		device_problem: null,
		last_run_at: null,
		last_run_canceled: false,
		measured_by_another_model: 0,
		references_without_pictures: 0,
		never_scanned: 0,
		scanned_under_older_rules: 0,
		unread_files: 0,
		installed: [],
		...over
	};
}

it('says the files not read yet after the files not scanned', () => {
	expect(facesStatus(feature({ never_scanned: 308, unread_files: 51793 }), true, 'GPU')).toBe(
		`Ready. Running on the GPU. 308 files not scanned for faces yet, and ${(51793).toLocaleString()} more waiting to be scanned.`
	);
});

it('says the files not read yet alone when every read file is scanned', () => {
	expect(facesStatus(feature({ unread_files: 1 }), true, 'GPU')).toBe(
		'Ready. Running on the GPU. 1 file waiting to be scanned.'
	);
});

it('says what it said before when nothing is unread', () => {
	expect(facesStatus(feature({ never_scanned: 1, unread_files: 0 }), true, 'CPU')).toBe(
		'Ready. Running on the CPU. 1 file not scanned for faces yet.'
	);
	expect(facesStatus(feature(), true, 'CPU')).toBe('Ready. Running on the CPU.');
});
