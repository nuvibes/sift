/* One History line's links, followed.
 *
 * A line naming a setting ("You changed Log detail") links to its row. Followed as an ordinary
 * navigation from a settings address that was opened directly, it would change only the route's
 * parameters, the panel would fade out and the screen would stand empty. So it opens the panel the way every
 * settings link does, and every other link stays the browser's.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import HistorySentence from './HistorySentence.svelte';
import type { HistoryPiece } from './history';

const opened = vi.hoisted(() => vi.fn((event: MouseEvent) => event.preventDefault()));
vi.mock('$lib/settings-ui/settings-view', () => ({ openSettingsInstead: opened }));

let host: HTMLElement;
let drawn: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	opened.mockClear();
});

function words(text: string): HistoryPiece {
	return { text, kind: null, id: null, href: null, gone: false, rest: [], lead: '' };
}

function thing(kind: string, id: string, text: string, href: string): HistoryPiece {
	return { text, kind, id, href, gone: false, rest: [], lead: '' };
}

function draw(pieces: HistoryPiece[]): HTMLAnchorElement {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(HistorySentence, { target: host, props: { pieces } });
	flushSync();
	return host.querySelector('a.named') as HTMLAnchorElement;
}

describe('a link in a History line', () => {
	it('opens a setting through the settings panel, at its section and row', () => {
		const link = draw([
			words('You changed '),
			thing('setting', 'logs.detail', 'Log detail', '/settings/logs#logs.detail')
		]);

		link.click();

		expect(opened).toHaveBeenCalledTimes(1);
		expect(opened.mock.calls[0].slice(1)).toEqual(['logs', 'logs.detail', undefined]);
	});

	it('leaves any other link to the browser', () => {
		const link = draw([words('Tagged '), thing('person', 'p-1', 'Wren Halloway', '/people/p-1')]);
		const stay = (event: Event) => event.preventDefault();
		document.addEventListener('click', stay);

		link.click();
		document.removeEventListener('click', stay);

		expect(opened).not.toHaveBeenCalled();
	});
});
