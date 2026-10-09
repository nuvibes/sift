/* Before its row and its files have answered, a collection's header says nothing it would take
 * back: no stand-in name, no letter for that name and no "0 files". */
import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const row = vi.hoisted(() => ({
	answers: false,
	value: { id: 'c1', name: 'Quillhouse picks', item_count: 14, size_bytes: 2048, art: null }
}));

vi.mock('$lib/api/client', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/api/client')>();
	const unanswered = vi.fn(() => new Promise(() => {}));
	// Only the row answers, and only when a test asks it to; the files never do.
	const get = vi.fn((path: string) =>
		row.answers && path === '/collections/c1' ? Promise.resolve(row.value) : unanswered()
	);
	return {
		...real,
		api: { get, post: unanswered, put: unanswered, del: unanswered }
	};
});

vi.mock('$app/navigation', () => ({
	goto: vi.fn(),
	replaceState: vi.fn(),
	pushState: vi.fn(),
	afterNavigate: vi.fn(),
	beforeNavigate: vi.fn()
}));
vi.mock('$app/state', () => ({
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false },
	page: {
		state: {},
		params: { id: 'c1' },
		url: new URL('http://localhost/collections/c1')
	}
}));

const Page = (await import('./+page.svelte')).default;

let host: HTMLElement | undefined;
let drawn: ReturnType<typeof mount> | undefined;

afterEach(() => {
	row.answers = false;
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
});

async function open(): Promise<string> {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Page, { target: host });
	for (let turn = 0; turn < 10; turn += 1) {
		flushSync();
		await Promise.resolve();
	}
	return (host.textContent ?? '').replace(/\s+/g, ' ');
}

it('draws no stand-in name, letter or count while nothing has answered', async () => {
	const said = await open();
	expect(said).not.toContain('Collection ');
	expect(said).not.toMatch(/\b0 files\b/);
	const letters = [...host!.querySelectorAll('.monogram')].map((one) => one.textContent?.trim());
	expect(letters.every((one) => !one)).toBe(true);
});

it('counts from the row while the files are still on their way, never 0', async () => {
	row.answers = true;
	const said = await open();
	expect(said).toContain('14 files');
	expect(said).not.toMatch(/\b0 files\b/);
});
