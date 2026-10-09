import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import Toaster from './Toaster.svelte';
// The component's own text, for the one check that has to read the stylesheet: jsdom lays nothing out.
import toasterSource from './Toaster.svelte?raw';
import { toasts } from '$lib/shell/toasts.svelte';
import { place, thing } from '$lib/components/common/toast-pieces';

/* A download's mark moves and others stand still: which toasts carry the class. */

let host: HTMLElement;
let mounted: Record<string, unknown>;

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(Toaster, { target: host }) as Record<string, unknown>;
	flushSync();
});

afterEach(() => {
	for (const toast of [...toasts.items]) toasts.dismiss(toast.id);
	flushSync();
	void unmount(mounted);
	host?.remove();
});

function mark(): HTMLElement | null {
	return host.querySelector('.mark');
}

describe('the mark that moves', () => {
	it('moves on the toast that stands while a fetch is running', () => {
		toasts.show('Downloading\u2026', { icon: 'download' });
		flushSync();

		expect(mark()?.classList.contains('fetching')).toBe(true);
	});

	it('does not move on the toast that says the fetch is over', () => {
		// Nearly the same words, opposite meaning.
		toasts.show('Downloaded. Drag it in from your downloads.');
		flushSync();

		expect(mark()?.classList.contains('fetching')).toBe(false);
	});

	it('does not move on an ordinary toast of any tone', () => {
		toasts.show('Could not stop that job.', { tone: 'error' });
		flushSync();

		expect(mark()?.classList.contains('fetching')).toBe(false);
	});
});

/* A named thing is a real link inside the sentence, and the whole sentence is what is heard. */
describe('a toast that names something', () => {
	it('draws the thing as a link to its page, where it sits in the sentence', () => {
		toasts.show(['3 files went into ', thing('collection', 'c1', 'Best of'), ' just now']);
		flushSync();

		const link = host.querySelector('a.named') as HTMLAnchorElement | null;
		expect(link?.getAttribute('href')).toBe('/collections/c1');
		expect(link?.textContent?.trim()).toBe('Best of');
		expect(host.querySelector('.message')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(
			'3 files went into Best of just now'
		);
	});

	it('draws a place as a link to its address', () => {
		toasts.show(['Follow it in ', place('Activity', '/settings/jobs')]);
		flushSync();

		expect(host.querySelector('a.named')?.getAttribute('href')).toBe('/settings/jobs');
	});

	it('gives the dismiss button the whole sentence, name and all', () => {
		toasts.show(['The file was tagged ', thing('tag', 't1', 'Beach')]);
		flushSync();

		expect(host.querySelector('.close')?.getAttribute('aria-label')).toBe(
			'Dismiss: The file was tagged Beach'
		);
	});

	it('draws no link at all for an ordinary message', () => {
		toasts.show('Link copied.');
		flushSync();

		expect(host.querySelector('a.named')).toBeNull();
	});
});

/* A long word stays inside the toast, asserted on the stylesheet since jsdom has no layout. */
describe('a long name', () => {
	const NAME = `Instagram${'810000000_1000000000001'.repeat(4)}.mp4`.slice(0, 90);

	/** One rule's body, comments removed so prose cannot stand in for the rule. */
	function ruleOf(selector: string): string {
		const styles = toasterSource.slice(toasterSource.lastIndexOf('<style>'));
		const start = styles.indexOf(`\t${selector} {`);
		const body = styles.slice(start, styles.indexOf('\n\t}', start));
		return body.replace(/\/\*[\s\S]*?\*\//g, '');
	}

	it('is said whole, not cut', () => {
		expect(NAME).toHaveLength(90);
		toasts.show(`Downloaded ${NAME}`, { tone: 'success' });
		flushSync();

		expect(host.querySelector('.message')?.textContent).toContain(NAME);
	});

	it('breaks inside the toast rather than running past its edge', () => {
		const message = ruleOf('.message');
		// The floor that lets the message be narrower than its longest word, and the break itself.
		expect(message).toContain('min-inline-size: 0;');
		expect(message).toContain('overflow-wrap: anywhere;');
	});
});

it('follows downloads and the benchmark for an admin only while the session is not locked', () => {
	expect(toasterSource).toMatch(/finishedDownloads\.follow\(session\.adminUnlocked\)/);
	expect(toasterSource).toMatch(/benchmarkRun\.follow\(session\.adminUnlocked\)/);
});
