/* More, the phone's list of Settings and the account. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { SETTINGS_SECTIONS } from '$lib/settings-ui/sections';

const mocks = vi.hoisted(() => ({
	admin: true,
	openSettingsInstead: vi.fn((event: MouseEvent) => event.preventDefault()),
	signOut: vi.fn(async () => undefined)
}));

vi.mock('$lib/shell/session.svelte', () => ({
	session: {
		get isAdmin() {
			return mocks.admin;
		},
		get viewer() {
			return { username: 'wren', role: mocks.admin ? 'admin' : 'guest' };
		}
	}
}));
vi.mock('$lib/settings-ui/settings-view', () => ({
	openSettingsInstead: mocks.openSettingsInstead
}));
vi.mock('$lib/shell/sign-out', () => ({ signOut: mocks.signOut }));

const Page = (await import('./+page.svelte')).default;

let host: HTMLElement | undefined;
let drawn: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
	vi.clearAllMocks();
});

function open(admin: boolean): HTMLElement {
	mocks.admin = admin;
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Page, { target: host });
	flushSync();
	return host;
}

function sections(page: HTMLElement): string[] {
	return [...page.querySelectorAll<HTMLAnchorElement>('a.go')].map(
		(row) => row.getAttribute('href') ?? ''
	);
}

describe('the More list', () => {
	it('lists every section of Settings for an admin, in the order Settings does', () => {
		expect(sections(open(true))).toEqual(SETTINGS_SECTIONS.map((one) => `/settings/${one.id}`));
	});

	it('offers a guest only the sections a guest can open', () => {
		expect(sections(open(false))).toEqual(
			SETTINGS_SECTIONS.filter((one) => !one.admin).map((one) => `/settings/${one.id}`)
		);
	});

	it('opens a section over this list rather than leaving it', () => {
		const page = open(true);
		const appearance = page.querySelector<HTMLAnchorElement>('a[href="/settings/appearance"]');

		appearance?.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));

		expect(mocks.openSettingsInstead).toHaveBeenCalledWith(expect.any(MouseEvent), 'appearance');
	});

	it('says who is signed in, and signs out from here', () => {
		const page = open(true);

		expect(page.textContent?.replace(/\s+/g, ' ')).toContain('Signed in as wren');
		const button = [...page.querySelectorAll('button')].find(
			(one) => one.textContent?.trim() === 'Sign out'
		);
		button?.click();
		flushSync();

		expect(mocks.signOut).toHaveBeenCalledOnce();
	});

	it('ends with Sign out, below every section, for an admin and a guest alike', () => {
		for (const admin of [true, false]) {
			const page = open(admin);
			const rows = [...page.querySelectorAll('a.go')];
			const button = [...page.querySelectorAll('button')].find(
				(one) => one.textContent?.trim() === 'Sign out'
			);
			expect(button, `admin ${admin}`).toBeDefined();
			/* Following: the button comes after the last section's row in the page's own order. */
			const after = rows.at(-1)!.compareDocumentPosition(button!);
			expect(after & Node.DOCUMENT_POSITION_FOLLOWING, `admin ${admin}`).toBeTruthy();
			unmount(drawn!);
			drawn = undefined;
			host?.remove();
		}
	});

	it('heads the sections with the groups Settings uses', () => {
		const text = open(true).textContent ?? '';

		for (const heading of ['Settings', 'Library', 'Recognition', 'Personal', 'System']) {
			expect(text).toContain(heading);
		}
	});
});
