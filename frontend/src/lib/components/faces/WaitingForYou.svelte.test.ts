import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick } from 'svelte';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import WaitingForYou from './WaitingForYou.svelte';
import source from './WaitingForYou.svelte?raw';

/*
 * The three numbers a person's page opens with: what you confirmed, what Sift recognized on its
 * own, and what is still waiting for an answer.
 *
 * The phrases are the same three on every faces screen: confirmed, recognized by Sift, needs
 * your input. What a beginner needs is whether Sift is asking them anything.
 *
 * Asserted together because they are one reading: "2,400 faces waiting" alone reads as a backlog,
 * and "12 confirmed" beside it shows a library Sift has been taught twelve things about.
 *
 * Three short lines, with the sentences in tooltips. What is pinned is that nothing was dropped
 * (each number still says what kind of face it counts, on hover and on focus) and that the two that
 * lead somewhere are links.
 */

vi.mock('$lib/people/faces.svelte', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	identifiedForPerson: vi.fn()
}));

const { identifiedForPerson } = await import('$lib/people/faces.svelte');
const asked = vi.mocked(identifiedForPerson);

let host: HTMLElement;

beforeEach(() => {
	vi.clearAllMocks();
});

afterEach(() => {
	host?.remove();
	// A tooltip is portalled to the end of the document, so removing the host leaves it behind.
	document.body.innerHTML = '';
});

/** The answer the server gives: all three counts on any page, whatever was filtered to. */
function counts(found: {
	confirmed: number;
	matched: number;
	suggested: number;
	unnamed?: number;
}) {
	asked.mockImplementation(async (_id, _page, attribution) => ({
		items: [],
		offset: 0,
		person_name: 'Marisol Vane',
		confirmed: found.confirmed,
		matched: found.matched,
		waiting: found.suggested,
		unnamed_from_folder: found.unnamed ?? 0,
		// The filtering's own number, which this strip must NOT read. See the ask below.
		total: attribution === undefined ? 0 : (found[attribution as keyof typeof found] ?? 0)
	}));
}

async function draw(): Promise<HTMLElement> {
	host = document.createElement('div');
	document.body.append(host);
	mount(WaitingForYou, { target: host, props: { personId: 'p1' } });
	await vi.waitFor(() => {
		flushSync();
		if (asked.mock.calls.length < 1) throw new Error('not asked yet');
	});
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
	return host;
}

describe('what Sift knows of one person', () => {
	it('says the three numbers in the order somebody reads them', async () => {
		counts({ confirmed: 12, matched: 300, suggested: 2400 });

		await draw();

		const said = [...host.querySelectorAll('.number')].map((one) => one.textContent?.trim());
		expect(said).toEqual([
			'You confirmed 12 as them',
			'Sift recognized 300 as them',
			'2,400 awaiting your input'
		]);
	});

	it('counts the folder files whose face is still unnamed, and opens exactly those in Browse', async () => {
		/* A folder's name filed them under this person and the face waits in an unnamed group, so
		   none of the three reaches it. The line is the way to them, on its own as well. */
		counts({ confirmed: 0, matched: 0, suggested: 0, unnamed: 800 });

		await draw();

		const line = [...host.querySelectorAll<HTMLAnchorElement>('a.number')].find((one) =>
			one.textContent?.includes('unnamed')
		);
		expect(line?.textContent?.trim()).toBe('800 files from the folder with a face still unnamed');
		expect(line?.getAttribute('href')).toBe('/browse?unnamed_face=p1');
	});

	it('says the waiting one in the singular when there is one of it', async () => {
		/* One press from empty is the state a person's page is most often looked at in, and "1 need
		   your input" is the fault a plural with a number in front of it always makes. */
		counts({ confirmed: 0, matched: 0, suggested: 1 });

		await draw();

		const said = [...host.querySelectorAll('.number')].map((one) => one.textContent?.trim());
		expect(said).toEqual(['1 awaiting your input']);
	});

	it('keeps the sentence that says what each number is, one hover away', async () => {
		/* In a tooltip rather than dropped. The block is 200 pixels wide under the cover, so nine
		   lines of text is not a shape it can take, but "300 matched" on its own does not say what
		   kind of face that is, and that is the sentence's job. */
		counts({ confirmed: 12, matched: 300, suggested: 2400 });

		await draw();

		const said: string[] = [];
		for (const line of host.querySelectorAll<HTMLElement>('.number')) {
			line.closest('.wrap')?.dispatchEvent(new PointerEvent('pointermove', { bubbles: true }));
			const bubble = await vi.waitFor(() => {
				flushSync();
				const found = document.querySelector('[role="tooltip"]');
				if (!found) throw new Error('no sentence yet');
				return found;
			});
			said.push((bubble.textContent ?? '').replace(/\s+/g, ' ').trim());
			line.closest('.wrap')?.dispatchEvent(new PointerEvent('pointerleave', { bubbles: true }));
			flushSync();
		}

		expect(said).toEqual([
			'Faces you confirmed to be them. These teach Sift what they look like.',
			'Faces Sift named without asking. Agree to each one from its card.',
			'Faces Sift thinks may be them, awaiting your input.'
		]);
	});

	it('names the person in the sentences when it is told who they are', async () => {
		/*
		 * The whole name where the sentence is about them, the first name where it is about what
		 * Sift learns: "Faces you confirmed to be Ada Byron. These teach Sift what Ada looks like."
		 */
		document.body.innerHTML = '';
		host = document.createElement('div');
		document.body.append(host);
		mount(WaitingForYou, { target: host, props: { personId: 'p1', name: 'Ada Byron' } });
		flushSync();
		await tick();
		await tick();
		flushSync();
		const said: string[] = [];
		for (const line of host.querySelectorAll<HTMLElement>('.number')) {
			line.closest('.wrap')?.dispatchEvent(new PointerEvent('pointermove', { bubbles: true }));
			const bubble = await vi.waitFor(() => {
				flushSync();
				const found = document.querySelector('[role="tooltip"]');
				if (!found) throw new Error('no sentence yet');
				return found;
			});
			said.push((bubble.textContent ?? '').replace(/\s+/g, ' ').trim());
			line.closest('.wrap')?.dispatchEvent(new PointerEvent('pointerleave', { bubbles: true }));
			flushSync();
		}
		expect(said[0]).toBe(
			'Faces you confirmed to be Ada Byron. These teach Sift what Ada looks like.'
		);
		expect(said[2]).toBe('Faces Sift thinks may be Ada Byron, awaiting your input.');
	});

	it('does not tell anybody to press words that may be inside a menu', async () => {
		/* The card at the other end leads with whichever act has more faces behind it, so a
		   sentence naming its button could name words that are behind a chevron on this very
		   person. Where to go is durable; what the control is called is not. */
		counts({ confirmed: 12, matched: 300, suggested: 2400 });

		await draw();

		const first = host.querySelector<HTMLElement>('a.number');
		first?.closest('.wrap')?.dispatchEvent(new PointerEvent('pointermove', { bubbles: true }));
		const bubble = await vi.waitFor(() => {
			flushSync();
			const found = document.querySelector('[role="tooltip"]');
			if (!found) throw new Error('no sentence yet');
			return found;
		});

		expect(bubble.textContent).not.toContain('These matches are right');
	});

	it('never says "her" or "him": Sift is not told anybody\'s pronouns', async () => {
		counts({ confirmed: 12, matched: 300, suggested: 2400 });

		await draw();

		expect(host.textContent).not.toMatch(/\b(her|his|him|she|he)\b/i);
	});

	it('sends the two presses somewhere, and leaves the first number as plain words', async () => {
		/* Confirming is done on the review screens, not from this strip: the number that teaches
		   Sift is a statement, and the two that ask for work are the ways in. The waiting one is a
		   row with its own Open on the right. */
		counts({ confirmed: 12, matched: 300, suggested: 2400 });

		await draw();

		const links = [...host.querySelectorAll('a')].map((one) => one.getAttribute('href'));
		expect(links).toEqual([
			'/organize/known-people/p1?show=matched',
			'/organize/known-people/p1?show=suggested'
		]);

		const row = host.querySelector('.asks .row');
		// The words are the way in, as the recognized line's are: a link, no button beside them.
		expect(row?.querySelector('button')).toBeNull();
		const open = row?.querySelector<HTMLAnchorElement>('a.number');
		expect(open?.getAttribute('href')).toBe('/organize/known-people/p1?show=suggested');
	});

	it('draws one line of help under the waiting row, only when it is given one', async () => {
		counts({ confirmed: 0, matched: 0, suggested: 9 });
		host = document.createElement('div');
		document.body.append(host);
		mount(WaitingForYou, {
			target: host,
			props: { personId: 'p1', help: 'Found with the starter pictures from StashDB.' }
		});
		await vi.waitFor(() => {
			flushSync();
			if (!host.querySelector('.asks')) throw new Error('not drawn yet');
		});

		expect(host.querySelector('.asks .row .number')?.textContent?.trim()).toBe(
			'9 awaiting your input'
		);
		expect(host.querySelector('.asks .help')?.textContent).toBe(
			'Found with the starter pictures from StashDB.'
		);
		/* The row and its help are one stack, the stack's gap between them, not two loose blocks. */
		const asks = host.querySelector('.asks') as HTMLElement;
		applyStyles(source, asks);
		expect(getComputedStyle(asks).display).toBe('flex');
		expect(getComputedStyle(asks).flexDirection).toBe('column');
		expect(getComputedStyle(asks.querySelector('.help') as HTMLElement).marginTop).toBe('0px');
		removeStyles();

		host.remove();
		await draw();
		await vi.waitFor(() => {
			flushSync();
			if (!host.querySelector('.asks')) throw new Error('not drawn yet');
		});
		expect(host.querySelector('.help')).toBeNull();
	});

	it('asks once, for one row, and narrows to nothing', async () => {
		/*
		 * One ask for all three counts: the page carries all three (`AppearancePage`), counted on
		 * the server, so there is no request per kind. One row, because the counts come with any
		 * page and none of the rows is drawn.
		 */
		counts({ confirmed: 12, matched: 300, suggested: 2400 });

		await draw();

		expect(asked).toHaveBeenCalledTimes(1);
		expect(asked.mock.calls[0][1]).toEqual({ limit: 1, offset: 0 });
		expect(asked.mock.calls[0][2], 'a narrowing would make total one of the three').toBeUndefined();
	});

	it('leaves out a number that is nought rather than offering a press with nothing behind it', async () => {
		counts({ confirmed: 12, matched: 0, suggested: 0 });

		await draw();

		expect(host.textContent).toContain('You confirmed 12 as them');
		expect(host.textContent).not.toContain('Sift recognized');
		expect(host.textContent).not.toContain('your input');
		expect(host.querySelectorAll('a').length).toBe(0);
	});

	it('says nothing at all where recognition was never switched on', async () => {
		/* Absent entirely rather than three zeroes: most installs never turn faces on, and a strip
		   of noughts over a feature nobody asked for reports a fault where there is none. */
		counts({ confirmed: 0, matched: 0, suggested: 0 });

		await draw();

		expect(host.querySelector('.counts')).toBeNull();
	});

	it('stays quiet when the numbers could not be read', async () => {
		/* Three lines on a screen that is about something else. The page is still worth reading. */
		asked.mockRejectedValue(new Error('nope'));

		await draw();

		expect(host.querySelector('.counts')).toBeNull();
	});
});
