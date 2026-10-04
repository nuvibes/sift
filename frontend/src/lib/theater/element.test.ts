import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { OWNED, settle, type Owned } from './element';

/*
 * The gate `element.ts` promises in its own header: a name in `OWNED` that `settle` does not write,
 * or a name `settle` writes that is not in `OWNED`, fails here.
 */
const source = readFileSync(resolve('src/lib/theater/element.ts'), 'utf8');
const body = source.slice(source.indexOf('export function settle('));
const written = new Set([...body.matchAll(/^\s*video\.(\w+)\s*=/gm)].map((m) => m[1]));

describe('the element values a cell owns', () => {
	it('are exactly the values settle writes, no more and no fewer', () => {
		expect([...written].sort()).toEqual([...OWNED].sort());
	});

	it('are every one written on a settle, whatever the element held before', () => {
		const video = {
			muted: false,
			volume: 0.2,
			loop: true,
			preload: 'auto',
			autoplay: true,
			playsInline: false,
			playbackRate: 3
		} as unknown as HTMLVideoElement;
		const owned: Owned = { muted: true, volume: 0.5, rate: 1.5 } as Owned;

		settle(video, owned);

		expect(video.muted).toBe(true);
		expect(video.volume).toBe(0.5);
		expect(video.loop).toBe(false);
		expect(video.preload).toBe('none');
		expect(video.autoplay).toBe(false);
		expect(video.playsInline).toBe(true);
		expect(video.playbackRate).toBe(1.5);
	});
});
