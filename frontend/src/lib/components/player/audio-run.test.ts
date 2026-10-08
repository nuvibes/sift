/* The Audio player passes pictures by: the walk stops on the first clip, or not at all. */

import { describe, expect, it } from 'vitest';
import { firstClip, MOST_PASSED } from './audio-run';

const kinds: Record<string, string> = { a: 'video', p1: 'image', p2: 'gif', c: 'video' };
const order = ['a', 'p1', 'p2', 'c'];
const recordOf = async (id: string) => ({ id, media_type: kinds[id] ?? 'image' });
const after = (from: string) => order[order.indexOf(from) + 1] ?? null;

describe('the Audio player walking a run', () => {
	it('passes every picture on to the next clip', async () => {
		expect((await firstClip('p1', after, recordOf))?.id).toBe('c');
	});

	it('stops on a clip it was handed', async () => {
		expect((await firstClip('a', after, recordOf))?.id).toBe('a');
	});

	it('finds nothing where only pictures are left, or the walk comes round', async () => {
		expect(await firstClip('p1', () => null, recordOf)).toBeNull();
		expect(await firstClip('p1', (from) => (from === 'p1' ? 'p2' : 'p1'), recordOf)).toBeNull();
	});

	it(`gives up after ${MOST_PASSED} pictures in a row`, async () => {
		let asked = 0;
		const endless = (from: string) => {
			asked += 1;
			return `${from}x`;
		};
		expect(await firstClip('p', endless, recordOf)).toBeNull();
		expect(asked).toBe(MOST_PASSED);
	});
});
