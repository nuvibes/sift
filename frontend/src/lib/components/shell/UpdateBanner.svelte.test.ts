import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import { session } from '$lib/shell/session.svelte';
import { updates, type UpdateState } from '$lib/shell/updates.svelte';
import UpdateBanner from './UpdateBanner.svelte';

/* The banner is the one part of this feature somebody sees without going looking for it, so what it
 * must not do matters more than what it does: not appear when there is nothing to say, not appear
 * for somebody who cannot act on it, and not come back once it has been dismissed.
 */

let host: HTMLElement;

const AVAILABLE: UpdateState = {
	current_version: '1.4.0',
	latest_version: '1.5.0',
	update_available: true,
	notes: 'Faster thumbnails.',
	release_page: '',
	last_checked: 0,
	dismissed: false
};

function render(state: UpdateState | null, { admin = true }: { admin?: boolean } = {}) {
	vi.spyOn(session, 'isAdmin', 'get').mockReturnValue(admin);
	vi.spyOn(updates, 'load').mockImplementation(async () => {
		updates.state = state;
		updates.loaded = state !== null;
	});
	// Set directly too: the banner reads the shared instance on its first render, before the
	// mocked load has resolved.
	updates.state = state;
	updates.loaded = state !== null;

	host = document.createElement('div');
	document.body.append(host);
	mount(UpdateBanner, { target: host });
	flushSync();
}

beforeEach(() => {
	updates.state = null;
	updates.loaded = false;
});

afterEach(() => {
	vi.restoreAllMocks();
	host?.remove();
	document.body.innerHTML = '';
});

describe('when an update is available', () => {
	it('names the version and links to the section that explains it', () => {
		render(AVAILABLE);

		expect(host.textContent).toContain('1.5.0 is available');
		const link = host.querySelector('a');
		expect(link?.getAttribute('href')).toBe('/settings/updates');
	});

	it('shows no command and no way to apply anything', () => {
		// The banner is a notice. The command lives on one screen, with the explanation around it:
		// a copyable command floating above every page is how somebody pastes one without context.
		render(AVAILABLE);

		expect(host.querySelector('code, pre')).toBeNull();
		expect(host.querySelectorAll('button')).toHaveLength(1);
		expect(host.querySelector('button')?.textContent?.trim()).toBe('Dismiss');
	});

	it('goes away when dismissed', async () => {
		render(AVAILABLE);
		vi.spyOn(updates, 'dismiss').mockImplementation(async () => {
			updates.state = { ...AVAILABLE, dismissed: true };
		});

		(host.querySelector('button') as HTMLButtonElement).click();
		await Promise.resolve();
		flushSync();

		expect(host.querySelector('.banner')).toBeNull();
	});
});

describe('when it must stay out of the way', () => {
	it('draws nothing before anything is known', () => {
		render(null);

		expect(host.querySelector('.banner')).toBeNull();
	});

	it('draws nothing when the running version is current', () => {
		render({ ...AVAILABLE, update_available: false, latest_version: '1.4.0' });

		expect(host.querySelector('.banner')).toBeNull();
	});

	it('draws nothing for a version already dismissed', () => {
		render({ ...AVAILABLE, dismissed: true });

		expect(host.querySelector('.banner')).toBeNull();
	});

	it('draws nothing for a guest, and does not even ask', () => {
		render(AVAILABLE, { admin: false });

		expect(host.querySelector('.banner')).toBeNull();
		expect(updates.load).not.toHaveBeenCalled();
	});
});
