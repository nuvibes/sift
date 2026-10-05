/* A guest is offered only what the server does for a guest, and a refused remove is said. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { session, type Viewer } from '$lib/shell/session.svelte';
import { toasts } from '$lib/shell/toasts.svelte';

const server = vi.hoisted(() => {
	class ApiError extends Error {
		constructor(
			readonly status: number,
			readonly detail: string | null
		) {
			super(detail ?? 'refused');
		}
	}
	return { ApiError, posts: [] as unknown[] };
});

vi.mock('$lib/api/client', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/api/client')>();
	const answer = vi.fn(async (path: string, options?: { body?: unknown }) => {
		if (path === '/collections/c1/items' && options?.body) {
			server.posts.push(options.body);
			throw new server.ApiError(403, 'Only an admin can do that.');
		}
		if (path === '/collections/c1/items')
			return {
				items: ['a0', 'a1'].map((id, at) => ({
					id,
					media_type: 'video',
					width: 1920,
					height: 1080,
					duration_ms: 4000,
					thumb: true,
					art: null,
					original_filename: `clip ${at}.mp4`,
					pinned: false,
					favorite: false,
					rating: null,
					position: null,
					concealed: false
				})),
				total: 2,
				limit: 50,
				offset: 0
			};
		if (path === '/collections/c1') return { id: 'c1', name: 'Harbour reel', item_count: 2 };
		if (path === '/collections/c1/tags') return [];
		return { items: [], total: 0 };
	});
	return {
		...real,
		ApiError: server.ApiError,
		api: { get: answer, post: answer, put: answer, del: answer }
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
	page: { state: {}, params: { id: 'c1' }, url: new URL('http://localhost/collections/c1') }
}));

const Page = (await import('./+page.svelte')).default;

let host: HTMLElement | undefined;
let drawn: ReturnType<typeof mount> | undefined;
let unsize: (() => void) | undefined;

/* A box with a size, since jsdom gives every element none. */
function sized(width: number, height: number): () => void {
	const was = ['clientWidth', 'clientHeight'].map(
		(name) => [name, Object.getOwnPropertyDescriptor(HTMLElement.prototype, name)] as const
	);
	for (const [name, value] of [
		['clientWidth', width],
		['clientHeight', height]
	] as const)
		Object.defineProperty(HTMLElement.prototype, name, { configurable: true, get: () => value });
	return () => {
		for (const [name, old] of was) if (old) Object.defineProperty(HTMLElement.prototype, name, old);
	};
}

beforeEach(() => {
	server.posts = [];
	unsize = sized(1200, 900);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
	session.viewer = undefined;
	unsize?.();
	document.querySelectorAll('.ui-menu').forEach((menu) => menu.remove());
});

async function open(role: 'admin' | 'guest') {
	session.viewer = { role, can_save_to_device: true } as Viewer;
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Page, { target: host });
	for (let turn = 0; turn < 10; turn += 1) {
		flushSync();
		await Promise.resolve();
	}
	flushSync();
}

/** The rows of the menu a right-click on the first tile opens, once it has its shared verbs. */
async function menuRows(): Promise<string[]> {
	host!
		.querySelector('[data-tile-id] [data-context-menu-trigger]')!
		.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 10, clientY: 10 }));
	let rows: string[] = [];
	await vi.waitFor(() => {
		rows = [...document.querySelectorAll('.ui-menu [role^="menuitem"]')].map((row) =>
			(row.textContent ?? '').trim()
		);
		expect(rows.some((row) => row.includes('Similar to this'))).toBe(true);
	});
	return rows;
}

describe("a collection's own verbs", () => {
	it('offers a guest neither the cover nor the remove', async () => {
		await open('guest');
		const rows = (await menuRows()).join(' | ');
		expect(rows).not.toContain('Use as the cover');
		expect(rows).not.toContain('Remove from this collection');
	});

	it('offers an admin both', async () => {
		await open('admin');
		const rows = (await menuRows()).join(' | ');
		expect(rows).toContain('Use as the cover');
		expect(rows).toContain('Remove from this collection');
	});

	it('says so when the server refuses a remove', async () => {
		await open('admin');
		await menuRows();
		const row = [...document.querySelectorAll<HTMLElement>('.ui-menu [role^="menuitem"]')].find(
			(one) => one.textContent?.includes('Remove from this collection')
		)!;
		row.click();
		const confirm = await vi.waitFor(() => {
			const button = [...document.querySelectorAll<HTMLButtonElement>('button')].find(
				(one) => one.textContent?.trim() === 'Remove'
			);
			expect(button).toBeDefined();
			return button!;
		});
		confirm.click();
		await vi.waitFor(() => expect(server.posts).toHaveLength(1));
		await vi.waitFor(() =>
			expect(toasts.items.some((toast) => toast.message === "Couldn't remove that")).toBe(true)
		);
	});
});
