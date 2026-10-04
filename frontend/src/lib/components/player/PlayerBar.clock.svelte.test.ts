/* The bar's clock and File info's Length are one length: the bar's half rounds as the record does,
 * and a finished file's playhead reads that length rather than a second short of it. */

import { afterEach, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import PlayerBar from './PlayerBar.svelte';
import { length } from '$lib/library/facts';

let host: HTMLElement;
let mounted: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
});

function clockAt(position: number, duration: number): string {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(PlayerBar, {
		target: host,
		props: {
			position,
			duration,
			onseek: () => {},
			playing: false,
			onplay: () => {},
			muted: false,
			volume: 1,
			onmute: () => {},
			onvolume: () => {}
		}
	});
	flushSync();
	/* The two ends of the scrub line, read as the one reading they were. */
	const end = (which: string) =>
		(host.querySelector(`.scrub-line .time.${which}`)?.textContent ?? '').trim();
	return `${end('start')} / ${end('end')}`;
}

it('says the length File info says for the same file', () => {
	expect(clockAt(120.7, 301.9)).toBe(`2:00 / ${length(301_900)}`);
	expect(length(301_900)).toBe('5:02');
});

it('reads the length at the end, never a second short of it', () => {
	expect(clockAt(301.9, 301.9)).toBe('5:02 / 5:02');
});
