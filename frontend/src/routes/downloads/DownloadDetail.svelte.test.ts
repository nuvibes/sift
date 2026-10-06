/* What the detail under a download row says.
 *
 * Two halves, and the order is what is being tested as much as the content: a row that failed shows
 * the failure and the way out of it FIRST, because that is what somebody opened the row for, and
 * the facts second. A row that did not fail has no card at all: a "nothing went wrong" panel on
 * every finished download is a sentence nobody reads on a screen that is already long.
 *
 * The two omissions are deliberate and are tested as omissions, because a label with nothing under
 * it reads as a fact Sift has lost rather than one it never had: `DownloadItem` carries no attempt
 * count and no downloader name, so there is no "Tried" row and no "Fetcher" row.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { words } from '$lib/design/testing.svelte';

import DownloadDetail from './DownloadDetail.svelte';
import type { DownloadItem } from './queue.svelte';

let host: HTMLElement;
let instance: ReturnType<typeof mount> | null = null;

function takeDown() {
	if (instance) unmount(instance);
	instance = null;
	host?.remove();
	document.body.innerHTML = '';
}

afterEach(takeDown);

function item(status: string, known: Partial<DownloadItem> = {}): DownloadItem {
	return {
		// Where in the line a waiting row stands; null for every other state.
		position: null,
		// The job that runs it, for the Activity screen's link; null once nothing does.
		job_id: null,
		// The failure sentence the server chooses; null is what every row not failed carries.
		sentence: null,
		username: null,
		asset_id: null,
		created_at: 0,
		creator_scope: null,
		dest_folder_id: null,
		// The folder it goes into, as the server names it; null is a download with nowhere to go.
		folder: null,
		error: null,
		error_code: null,
		error_tier: null,
		filename: 'clip.mp4',
		finished_at: null,
		id: 'one',
		person_id: null,
		site: 'example.test',
		site_id: null,
		progress: null,
		remembered_filename: null,
		files_offered: null,
		files_left_out: null,
		reads_refused: null,
		shown_url: 'example.test/clip',
		site_key: null,
		site_name: null,
		size_bytes: null,
		status,
		url: 'https://example.test/clip',
		via: null,
		via_address: null,
		...known
	};
}

function render(one: DownloadItem, more: Record<string, unknown> = {}) {
	takeDown();
	host = document.createElement('div');
	document.body.append(host);
	instance = mount(DownloadDetail, {
		target: host,
		props: { item: one, now: 600, ...more }
	});
	flushSync();
}

/** Every term in the facts list, in the order they are drawn. */
function terms(): string[] {
	return [...host.querySelectorAll('dt')].map((one) => words(one));
}

/** What is under one of them. */
function valueOf(term: string): string {
	const all = [...host.querySelectorAll('dt')];
	const at = all.findIndex((one) => words(one) === term);
	return at < 0 ? '' : words(host.querySelectorAll('dd')[at]);
}

describe('the facts', () => {
	it('names only what the wire actually carries', () => {
		render(item('done', { asset_id: 'a1' }));
		expect(terms()).toEqual(['Link', 'Site', 'IP used', 'Saved to', 'Added', 'File']);
		// The two in the design that the server does not send. A label with nothing under it reads
		// as a fact Sift has lost; these are facts it has never been told.
		expect(terms()).not.toContain('Tried');
		expect(terms()).not.toContain('Fetcher');
	});

	it('says whose IP it used: your own, or the tunnel by name', () => {
		render(item('done', { asset_id: 'a1', via: 'Direct' }));
		expect(valueOf('IP used')).toBe('Your own');
		expect(terms()).not.toContain('Tunnel');
		render(item('done', { asset_id: 'a1', via: 'Iceland' }));
		expect(valueOf('Tunnel')).toBe('Iceland');
		expect(terms()).not.toContain('IP used');
	});

	/* The address beside the tunnel's name, covered as Settings covers it. The one RECORDED when
	   the download went (a provider can move a tunnel to another server under the same name),
	   so it comes off the row, never off today's tunnel list. */
	it('puts the server the download went out through beside the tunnel, most of it covered', () => {
		render(item('failed', { via: 'Iceland', via_address: '192.0.2.44' }));
		expect(valueOf('Tunnel')).toBe('Iceland');
		// Under its own label: the address is the SERVER's, not necessarily the one the site saw.
		expect(terms()).toContain('Tunnel server');
		expect(terms()).not.toContain('IP used');
		const shown = host.querySelector('.via');
		expect(shown).not.toBeNull();
		// The first part plain, the rest painted over by the shared control, never left bare.
		const covered = shown?.querySelector('.covered');
		expect(covered?.textContent).toBe('0.2.44');
		expect(shown?.querySelector('button')?.getAttribute('aria-label')).toBe(
			'Show the whole of the address of the server Iceland was connected to'
		);
		// No address recorded: the name alone, and no control or label pretending there is one.
		render(item('failed', { via: 'Iceland', via_address: null }));
		expect(valueOf('Tunnel')).toBe('Iceland');
		expect(terms()).not.toContain('Tunnel server');
		expect(host.querySelector('.covered')).toBeNull();
	});

	/* "Your own" is wrong for a row with no route recorded at all: a download not sent yet, refused
	   before anything was sent, or older than the record. None of those went out of your own IP. */
	it('says the route was not recorded rather than claiming your own IP', () => {
		render(item('queued', { via: null }));
		expect(valueOf('IP used')).toBe('Not recorded');
	});

	it('says Saved to once there is a file, and Saving to while it is still on its way', () => {
		render(item('done', { asset_id: 'a1' }));
		expect(terms()).toContain('Saved to');
		render(item('running'));
		expect(terms()).toContain('Saving to');
		expect(terms()).not.toContain('Saved to');
	});

	/* Only a running download is "Saving to"; every other state names the folder it would use. */
	it.each(['failed', 'queued', 'blocked', 'paused', 'canceled'])(
		'says Save to on a %s download, never Saving to',
		(status) => {
			render(item(status));
			expect(terms()).toContain('Save to');
			expect(terms()).not.toContain('Saving to');
			expect(terms()).not.toContain('Saved to');
		}
	);

	it('names the actual folder and where it is, never "the default folder"', () => {
		// Nothing chosen is not nothing: the download went to the folder the settings give it, and
		// the server sends that folder by name and path. "The default folder" named nothing.
		render(
			item('done', {
				asset_id: 'a1',
				folder: { id: 'f1', name: 'Sift Downloads', path: 'D:\\Media\\Sift Downloads' }
			})
		);
		expect(valueOf('Saved to')).toBe('Sift Downloads D:\\Media\\Sift Downloads');
		expect(host.querySelector('dd .folder')?.textContent).toBe('Sift Downloads');
		expect(host.querySelector('dd .where')?.textContent).toBe('D:\\Media\\Sift Downloads');
		expect(words(host)).not.toContain('default folder');
	});

	it('says there is no folder when none is set anywhere, in those words', () => {
		render(item('queued', { folder: null }));
		expect(valueOf('Save to')).toBe('No download folder');
	});

	it('opens the file through the page rather than through the address', () => {
		/* `/asset/{id}` as a bare anchor runs the route written for somebody arriving cold: the
		   screen underneath is torn down, and closing goes to the library instead of back. */
		const onopen = vi.fn();
		render(item('done', { asset_id: 'a1' }), { onopen });
		const button = [...host.querySelectorAll('button')].find(
			(one) => words(one) === 'clip.mp4'
		) as HTMLButtonElement;
		button.click();
		expect(onopen).toHaveBeenCalledWith('a1');
		expect(host.querySelector('a[href="/asset/a1"]')).toBeNull();
	});
});

describe('a file that has since been deleted', () => {
	const deleted = () =>
		item('done', { filename: null, asset_id: null, remembered_filename: 'clip.mp4' });

	it('keeps the name, strikes it through, and leads nowhere', () => {
		render(deleted(), { gone: true, onopen: vi.fn() });
		expect(valueOf('File')).toBe('clip.mp4');
		expect(host.querySelector('.gone')).not.toBeNull();
		expect(host.querySelectorAll('a').length).toBe(0);
	});

	it('will not offer the address either', () => {
		// The address is what it WAS fetched from, and offering to open it reads as offering the
		// file back.
		render(deleted(), { gone: true });
		expect(host.querySelector('a.address')).toBeNull();
		expect(valueOf('Link')).toBe('example.test/clip');
	});
});

describe('the held card', () => {
	/* A pause raises exactly one question (are the bytes still there?), and the card answers it
	   before anything else on the screen. It is NOT the failure card in a third colour: nothing
	   went wrong, so there is no red line and no code underneath. */
	it('says what a pause means, and offers the way out of it', () => {
		const onresume = vi.fn();
		render(item('paused'), { onresume });
		expect(words(host.querySelector('.held .consequence'))).toBe(
			'Paused. What is downloaded so far is kept.'
		);
		// Not the app's failure line: that is red, and red is for things that are actually wrong.
		expect(host.querySelector('.problem')).toBeNull();
		const button = host.querySelector('.held button') as HTMLButtonElement;
		expect(words(button)).toBe('Resume');
		button.click();
		expect(onresume).toHaveBeenCalledWith('one');
	});

	it('is drawn on nothing else', () => {
		render(item('running'), { onresume: vi.fn() });
		expect(host.querySelector('.held')).toBeNull();
		render(item('failed'), { sentence: 'The Site refused the request.', onresume: vi.fn() });
		expect(host.querySelector('.held')).toBeNull();
	});
});

describe('the failure card', () => {
	it('is drawn only where something failed', () => {
		render(item('done', { asset_id: 'a1' }), { sentence: null });
		expect(host.querySelector('.failure')).toBeNull();
		render(item('failed'), { sentence: 'The Site refused the request.' });
		expect(host.querySelector('.failure')).not.toBeNull();
	});

	it('says what happened, what it cost, and the code underneath', () => {
		render(item('failed', { error_code: 'forbidden', error_tier: 2 }), {
			sentence: 'The Site refused the request.',
			raw: 'forbidden \u2014 from example.test \u2014 tier 2'
		});
		expect(words(host.querySelector('.problem'))).toBe('The Site refused the request.');
		expect(words(host.querySelector('.consequence'))).toBe(
			"Nothing was downloaded. There's no file in your library from this link."
		);
		expect(words(host.querySelector('.raw'))).toBe(
			'forbidden \u2014 from example.test \u2014 tier 2'
		);
	});

	it('offers cookies on a blocked row, and says cookies rather than login', () => {
		const oncookies = vi.fn();
		render(item('blocked'), {
			sentence: 'This Site wants to know who is asking.',
			oncookies,
			onretry: vi.fn()
		});
		expect(words(host.querySelector('.consequence'))).toBe(
			'Nothing will be downloaded from this Site until Sift has cookies for it.'
		);
		const fixes = [...(host.querySelector('.fixes')?.querySelectorAll('button') ?? [])].map((one) =>
			words(one)
		);
		expect(fixes).toEqual(['Add cookies', 'Try again', 'Copy the details']);
		expect(host.textContent?.toLowerCase()).not.toContain('login');
		(host.querySelector('.fixes button') as HTMLButtonElement).click();
		expect(oncookies).toHaveBeenCalledWith('example.test');
	});

	it('does not offer cookies on an ordinary failure', () => {
		render(item('failed'), {
			sentence: 'The Site refused the request.',
			oncookies: vi.fn(),
			onretry: vi.fn()
		});
		const fixes = [...(host.querySelector('.fixes')?.querySelectorAll('button') ?? [])].map((one) =>
			words(one)
		);
		expect(fixes).toEqual(['Try again', 'Copy the details']);
	});
});
