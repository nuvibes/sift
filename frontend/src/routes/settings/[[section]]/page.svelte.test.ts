/*
 * Settings opened at its own address.
 *
 * A guest at the address of an admin's pane lands on their own first section before the pane can
 * ask the server for anything it would refuse, and the address says where they landed. An address
 * that is no section at all says so in one sentence.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const at = vi.hoisted(() => ({ section: 'library' as string | undefined, admin: false }));
const entered = vi.hoisted(() => vi.fn());
const replaced = vi.hoisted(() => vi.fn());

vi.mock('$app/state', () => ({
	page: {
		state: {},
		get params() {
			return { section: at.section };
		},
		get url() {
			return new URL(`http://localhost/settings/${at.section ?? ''}`);
		}
	}
}));
vi.mock('$app/navigation', () => ({ replaceState: replaced, goto: vi.fn() }));
vi.mock('$lib/settings-ui/settings-view', () => ({ enterSettings: entered }));
vi.mock('$lib/shell/session.svelte', () => ({
	session: {
		get isAdmin() {
			return at.admin;
		}
	}
}));

const Page = (await import('./+page.svelte')).default;

let host: HTMLElement | undefined;
let drawn: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
	vi.clearAllMocks();
});

function open(section: string | undefined, admin: boolean): HTMLElement {
	at.section = section;
	at.admin = admin;
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Page, { target: host });
	flushSync();
	return host;
}

describe('settings at its own address', () => {
	it("sends a guest from an admin's pane to their own first section", () => {
		open('library', false);
		expect(replaced).toHaveBeenCalledWith('/settings/playback', {});
		expect(entered).toHaveBeenCalledWith('playback');
		expect(entered).not.toHaveBeenCalledWith('library', undefined, undefined);
	});

	it('opens the pane an admin asked for', () => {
		open('library', true);
		expect(replaced).not.toHaveBeenCalled();
		expect(entered).toHaveBeenCalledWith('library', undefined, undefined);
	});

	it('says in one sentence that an address is no settings page', () => {
		const page = open('nowhere', true);
		expect(entered).not.toHaveBeenCalled();
		expect(page.textContent?.replace(/\s+/g, ' ')).toContain("That address isn't a settings page.");
		expect(page.textContent).not.toContain('Nothing here yet');
	});
});
