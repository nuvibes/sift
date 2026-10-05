/* A link dropped on an entity page is fetched, and fetching is an admin's: a guest is not offered it. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import EntityDropZone from './EntityDropZone.svelte';
import { pageAim } from './aimed-page.svelte';

const who = vi.hoisted(() => ({ isAdmin: true }));
vi.mock('$lib/shell/session.svelte', () => ({ session: who }));

const fetched = vi.hoisted(() => vi.fn());
vi.mock('$lib/library/aimed-drop.svelte', async (original) => ({
	...(await original<typeof import('$lib/library/aimed-drop.svelte')>()),
	fetchOnto: fetched
}));

const LINK = 'https://example.invalid/clip';
let mounted: Record<string, unknown> | null = null;
let host: HTMLElement;

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	fetched.mockReset();
});

function render(isAdmin: boolean) {
	who.isAdmin = isAdmin;
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(EntityDropZone, {
		target: host,
		props: { kind: 'tag', id: 't1', name: 'seagrass' }
	});
	flushSync();
}

function drag(type: string) {
	const event = new Event(type, { bubbles: true, cancelable: true });
	Object.defineProperty(event, 'dataTransfer', {
		value: {
			types: ['text/uri-list'],
			getData: (as: string) => (as === 'text/uri-list' ? LINK : '')
		}
	});
	window.dispatchEvent(event);
	flushSync();
}

describe('a link held over an entity page', () => {
	it('is offered to an admin and fetched under the page', () => {
		render(true);
		expect(pageAim()).toEqual({ kind: 'tag', id: 't1', name: 'seagrass' });
		drag('dragenter');
		drag('dragover');
		expect(host.querySelector('.overlay')).not.toBeNull();
		drag('drop');
		expect(fetched).toHaveBeenCalledWith(LINK, 'tag', 't1', 'seagrass');
	});

	it('is left to the window for a guest, which neither offers nor fetches it here', () => {
		render(false);
		expect(pageAim()).toBeNull();
		drag('dragenter');
		drag('dragover');
		expect(host.querySelector('.overlay')).toBeNull();
		drag('drop');
		expect(fetched).not.toHaveBeenCalled();
	});
});
