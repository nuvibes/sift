import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount } from 'svelte';

import FileName, { nameRuns } from './FileName.svelte';

let host: HTMLElement;
let shown: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (shown) unmount(shown);
	shown = null;
	host?.remove();
});

describe('where a filename may wrap', () => {
	it('wraps between the words of a name, never inside one', () => {
		expect(nameRuns('QuietHarbourDawn.mp4')).toEqual(['Quiet', 'Harbour', 'Dawn.', 'mp4']);
		expect(nameRuns('nadia_vance-02.jpg')).toEqual(['nadia_', 'vance-', '02.', 'jpg']);
	});

	it('leaves a name with nowhere to wrap whole', () => {
		expect(nameRuns('c08b10c76eb8')).toEqual(['c08b10c76eb8']);
		expect(nameRuns('')).toEqual([]);
	});

	it('draws the name as its own text, with a place to wrap at each boundary', () => {
		host = document.createElement('p');
		document.body.append(host);
		shown = mount(FileName, { target: host, props: { name: 'QuietHarbourDawn.mp4' } });

		expect(host.textContent).toBe('QuietHarbourDawn.mp4');
		expect(host.querySelectorAll('wbr')).toHaveLength(3);
	});
});
