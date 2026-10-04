/* SPDX-License-Identifier: AGPL-3.0-or-later */
import { afterEach, expect, it, vi } from 'vitest';
import { drawsHeic, forgetHeic } from './heic';

/** A browser with no `ImageDecoder` (plain http), whose pictures load or fail as told. */
function drawing(answer: 'load' | 'error'): string[] {
	const handed: string[] = [];
	vi.stubGlobal('ImageDecoder', undefined);
	vi.stubGlobal(
		'Image',
		class {
			naturalWidth = 0;
			onload: (() => void) | null = null;
			onerror: (() => void) | null = null;
			set src(value: string) {
				handed.push(value);
				queueMicrotask(() => {
					if (answer === 'load') {
						this.naturalWidth = 8;
						this.onload?.();
					} else this.onerror?.();
				});
			}
		}
	);
	return handed;
}

afterEach(() => {
	vi.unstubAllGlobals();
	forgetHeic();
});

it('asks by drawing a sample where the browser has no decoder to ask, and asks nothing of the network', async () => {
	const handed = drawing('error');

	expect(await drawsHeic()).toBe(false);
	expect(handed).toHaveLength(1);
	expect(handed[0].startsWith('data:image/heic;base64,')).toBe(true);
});

it('takes a drawn sample as a yes', async () => {
	drawing('load');

	expect(await drawsHeic()).toBe(true);
});

it('has no answer where it can neither ask nor draw', async () => {
	vi.stubGlobal('ImageDecoder', undefined);
	vi.stubGlobal('Image', undefined);

	expect(await drawsHeic()).toBeNull();
});

it('takes the decoder at its word and draws nothing', async () => {
	const handed = drawing('load');
	vi.stubGlobal('ImageDecoder', { isTypeSupported: async () => false });

	expect(await drawsHeic()).toBe(false);
	expect(handed).toEqual([]);
});
