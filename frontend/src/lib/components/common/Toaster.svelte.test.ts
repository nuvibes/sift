import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import Toaster from './Toaster.svelte';
// The component's own text, for the one check that has to read the stylesheet: jsdom lays nothing out.
import toasterSource from './Toaster.svelte?raw';
import { toasts } from '$lib/shell/toasts.svelte';
import { place, thing } from '$lib/components/common/toast-pieces';

/*
 * The mark on a toast, and the one that MOVES.
 *
 * Most toasts announce something that already happened and their mark is a still glyph chosen by
 * the tone. A download is the other kind: it says a thing is happening now, and it stands until the
 * thing stops. A still arrow beside "Downloading..." looks exactly like a still arrow beside
 * "Downloaded", so the motion is what separates the two, which makes it a fact worth a test rather
 * than decoration.
 *
 * The movement itself is a CSS animation and jsdom computes no animations, so what is pinned here is
 * the thing that decides whether the rule applies at all: which toasts carry the class, and which
 * must not.
 */

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
		// The words are nearly the same and the meaning is opposite. If both moved, the moving mark
		// would say nothing at all.
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

/*
 * The linked name, drawn as part of the sentence rather than as a control beside it.
 *
 * A toast that says "added to the photo set beach days" and gives no way to go and look at it is a
 * message about something you then have to find. What this pins is that the name is an anchor with
 * a real address, and that the sentence a screen reader is handed is the whole one: the buttons
 * are named after the message, and a lead with the name missing names the wrong thing.
 */
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

/*
 * A long word stays inside the toast.
 *
 * A downloaded file's name is ONE word to the layout, and a flex item's floor is its longest word,
 * so "Downloaded Instagram810000000_10000000001..." would run out of the toast's right edge.
 * The rule is the primitive's, so every caller has it without asking.
 *
 * Asserted against the STYLESHEET as well as the text, because jsdom has no layout: a rendered
 * width here would pass with the rule deleted. The box itself is looked at in the real window.
 */
describe('a long name', () => {
	const NAME = `Instagram${'810000000_1000000000001'.repeat(4)}.mp4`.slice(0, 90);

	/** One rule's body, from the stylesheet alone, with its comments taken out so a phrase in a
	 *  comment cannot stand in for the rule. */
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
