/* The folder suggestions list, and the two things about it that matter most.
 *
 * Saying yes is ONE request. Anything that turned it into two (name the group, then attribute
 * the files) would be the point of the screen missed, and it is the kind of change that looks
 * harmless in a diff and only shows up as a half-applied answer on somebody's screen.
 *
 * And a suggestion that rests on nothing but a folder name says so on the row. That is a different
 * kind of answer from one a face agrees with, and somebody deciding needs to know which they are
 * looking at without opening anything.
 */
import { BLANK_PICTURE } from '$lib/people/faces.svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

import { replaceState } from '$app/navigation';
import type { PagerProps } from '$lib/components/common/Pager.svelte';
import FolderSuggestions from './FolderSuggestions.svelte';
import source from './FolderSuggestions.svelte?raw';
import pressable from '$lib/components/common/Pressable.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import type { Proposal } from '$lib/search/suggestions.svelte';

const suggestions = vi.fn();
const confirmSuggestion = vi.fn();
const rejectSuggestion = vi.fn();
const sayWhoAFolderIs = vi.fn();

vi.mock('$lib/search/suggestions.svelte', () => ({
	SUGGESTIONS_PER_PAGE: 50,
	suggestions: (...a: unknown[]) => suggestions(...a),
	confirmSuggestion: (...a: unknown[]) => confirmSuggestion(...a),
	rejectSuggestion: (...a: unknown[]) => rejectSuggestion(...a),
	sayWhoAFolderIs: (...a: unknown[]) => sayWhoAFolderIs(...a)
}));

/* The address, held so one test can arrive here pointed at a folder. `test-setup` supplies a fixed
   URL for every screen that reads one, and this screen reads the FRAGMENT: a still on the board's
   Folders card names the card it came from. A mock declared here takes precedence over that one. */
const at = vi.hoisted(() => ({ url: new URL('http://localhost/organize/folders') }));
vi.mock('$app/state', () => ({
	page: {
		get url() {
			return at.url;
		},
		state: {},
		params: {}
	},
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false }
}));

const shown = vi.fn();
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: (t: string) => shown(t) } }));
/* Every decision on this screen goes through `decided`, which says what happened and offers to take
   it back. Stubbed onto the same reporter as the toast, so what a decision SAYS is asserted the
   same way here whichever of the two it went through. */
vi.mock('$lib/organize/organize.svelte', () => ({
	decided: (message: string) => shown(message),
	waiting: { count: 0, check: () => {} }
}));
vi.mock('$lib/library/changes.svelte', async () => ({
	...(await vi.importActual<typeof import('$lib/library/changes.svelte')>(
		'$lib/library/changes.svelte'
	)),
	reloadOnLibraryChange: () => {}
}));
/* Nothing is stood in for here. The hidden-mark fallback is a rule of ITS own, and so is
   the crop's address: the address carrying the row's token is what lets the browser keep the
   picture, so a stand-in for `cropUrl` would leave this file asserting its own double. */
vi.mock('$lib/people/faces.svelte', async () => ({
	...(await vi.importActual<typeof import('$lib/people/faces.svelte')>('$lib/people/faces.svelte'))
}));
vi.mock('$lib/entity/art', async (importActual) => ({
	...(await importActual<typeof import('$lib/entity/art')>()),
	thumbUrl: (item: { id: string }) => `/api/assets/${item.id}/thumb`
}));

function proposal(over: Partial<Proposal> = {}): Proposal {
	return {
		id: 'claim-1',
		kind: 'person',
		proposed: 'nadia vance',
		evidence: 'face_group',
		folder: 'Nadia Vance',
		folder_id: 'folder-1',
		path: 'Models/Nadia Vance',
		files: 47,
		art: {},
		group_id: 'pile-1',
		face_id: 'face-1',
		face_art: 'face-token',
		near_miss: null,
		site: null,
		is_username: false,
		dissenting: [],
		per_file: [],
		cover: '',
		...over
	};
}

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	at.url = new URL('http://localhost/organize/folders');
	suggestions.mockResolvedValue({ proposals: [proposal()], total: 1 });
	confirmSuggestion.mockResolvedValue({
		person_id: 'person-1',
		created: true,
		files: 47,
		faces: 12,
		alias: true,
		username_linked: false,
		// The record the decision wrote. A double missing a field the component reads would leave
		// that whole branch undrawn with the test still green.
		decision_id: 'decision-1'
	});
	rejectSuggestion.mockResolvedValue({ settled: true, decision_id: 'decision-2' });
});

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
});

async function render(): Promise<HTMLElement> {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(FolderSuggestions, { target: host }) as Record<string, unknown>;
	flushSync();
	for (let turn = 0; turn < 4; turn += 1) await tick();
	flushSync();
	return host;
}

function button(root: HTMLElement, words: string): HTMLButtonElement | undefined {
	return [...root.querySelectorAll<HTMLButtonElement>('button')].find((one) =>
		one.textContent?.includes(words)
	);
}

/* An answer behind the chevron, as somebody reaches it: the menu opens on POINTERDOWN, not on
   click, and its rows are portalled to the end of the document. */
async function behind(root: HTMLElement, words: string): Promise<HTMLElement | undefined> {
	const door = root.querySelector<HTMLButtonElement>('.split .trail button');
	door?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
	flushSync();
	for (let turn = 0; turn < 2; turn += 1) await tick();
	return [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((one) =>
		one.textContent?.includes(words)
	);
}

async function answer(root: HTMLElement, words: string): Promise<void> {
	const row = await behind(root, words);
	if (!row) throw new Error(`there is no answer saying ${words}`);
	row.click();
	flushSync();
	for (let turn = 0; turn < 4; turn += 1) await tick();
}

describe('answering a folder', () => {
	it('says yes in one request, with nothing left out', async () => {
		const root = await render();

		button(root, 'Yes')?.click();
		for (let turn = 0; turn < 4; turn += 1) await tick();

		expect(confirmSuggestion).toHaveBeenCalledTimes(1);
		expect(confirmSuggestion).toHaveBeenCalledWith('claim-1', []);
	});

	it('holds only the card answering, and every other card stays pressable', async () => {
		suggestions.mockResolvedValue({
			proposals: [proposal(), proposal({ id: 'claim-2', folder_id: 'folder-2' })],
			total: 2
		});
		confirmSuggestion.mockReturnValue(new Promise(() => {}));
		const root = await render();
		const yesOn = (id: string) => button(root.querySelector<HTMLElement>(`#claim-${id}`)!, 'Yes');

		yesOn('claim-1')?.click();
		flushSync();
		await tick();

		expect(root.querySelector<HTMLButtonElement>('#claim-claim-1 button')?.disabled).toBe(true);
		expect(yesOn('claim-2')?.disabled).toBe(false);
	});

	it('leaves out the files somebody unticked, and only those', async () => {
		suggestions.mockResolvedValue({
			proposals: [proposal({ dissenting: ['asset-a', 'asset-b'] })],
			total: 1
		});
		const root = await render();

		// The odd ones out are behind one line on the card, so every card is the same height as
		// its neighbours until somebody asks. Opened here, as somebody would.
		button(root, 'do not match')?.click();
		flushSync();
		/* The whole ROW is the control and the box inside it is a picture of the state, so what is
		   pressed and what is read are the row's: `aria-pressed`, on the `Pressable`. */
		const boxes = [...root.querySelectorAll<HTMLButtonElement>('.ticked')];
		expect(boxes).toHaveLength(2);
		// Ticked to start with: a folder named after somebody usually is all of them, and the odd
		// ones out are offered to be removed rather than added.
		expect(boxes.every((one) => one.getAttribute('aria-pressed') === 'true')).toBe(true);

		boxes[0].click();
		flushSync();
		button(root, 'Yes')?.click();
		for (let turn = 0; turn < 4; turn += 1) await tick();

		expect(confirmSuggestion).toHaveBeenCalledWith('claim-1', ['asset-a']);
	});

	it('refuses one permanently, and says so', async () => {
		const root = await render();

		await answer(root, 'No, not a person');

		expect(rejectSuggestion).toHaveBeenCalledWith('claim-1');
		expect(shown.mock.calls.at(-1)?.[0]).toContain("not a person. Sift won't ask about it again.");
	});
});

/*
 * The question is asked in words, worded per kind, because the three kinds do different things on
 * a yes: a label promising a person on a row whose yes names nobody would be a lie on the button
 * that does the most.
 */
describe('the last page, emptied', () => {
	/* A queue of `size` folders, answered as the server answers: the rows from `offset`, the total. */
	function queueOf(size: () => number) {
		return async (query: { limit: number; offset?: number; from?: string }) => {
			const offset = Number(query.offset ?? 0);
			const count = Math.max(0, Math.min(query.limit, size() - offset));
			return {
				proposals: Array.from({ length: count }, (_x, i) =>
					proposal({ id: `claim-${offset + i}`, proposed: `nadia vance n${offset + i}` })
				),
				total: size(),
				offset
			};
		};
	}

	it('steps back to the page the queue now ends on, and the address follows', async () => {
		/* Fifty-one folders, on the second page, and its one card answered: the page is empty and
		   the queue is not. The shared rule in `CardPaging` shows the page the queue now ends on. */
		let size = 51;
		suggestions.mockImplementation(queueOf(() => size));
		let pager: PagerProps | null = null;
		host = document.createElement('div');
		document.body.append(host);
		mounted = mount(FolderSuggestions, {
			target: host,
			props: { onpaging: (reported: PagerProps | null) => (pager = reported) }
		}) as Record<string, unknown>;
		flushSync();
		for (let turn = 0; turn < 4; turn += 1) await tick();
		(pager as unknown as PagerProps).onnext();
		for (let turn = 0; turn < 6; turn += 1) await tick();
		flushSync();
		expect(host.textContent).toContain('nadia vance n50');

		size = 50;
		button(host, 'Yes')?.click();
		for (let turn = 0; turn < 8; turn += 1) await tick();
		flushSync();

		expect(suggestions).toHaveBeenLastCalledWith({ limit: 50, offset: 0 });
		expect(host.textContent).not.toContain('Nothing to review');
		expect(host.textContent).toContain('nadia vance n0');
		expect(vi.mocked(replaceState).mock.calls.at(-1)?.[0]).toBe(
			'/organize/folders?from=claim-0&near=0'
		);
	});
});

describe('what the card asks', () => {
	function question(root: HTMLElement): string {
		return root.querySelector('.foot .section-heading')?.textContent?.trim() ?? '';
	}

	it('asks in the one card shape, with the yes as the button and the rest behind it', async () => {
		const root = await render();

		expect(question(root)).toBe('Is \u2018nadia vance\u2019 a person?');
		// At the foot, under the evidence, with why Sift asks on the line beneath it.
		const card = root.querySelector('li');
		expect(
			card?.querySelector('.where')?.compareDocumentPosition(card.querySelector('.foot') as Node)
		).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
		expect(card?.querySelector('.foot .detail')?.textContent).toBe(
			'The same face runs through this folder'
		);
		expect(card?.querySelector('.foot .split .lead button')?.textContent).toContain(
			'Yes, a person'
		);
		// One button on the card answers the question; the other two are a menu away.
		expect(button(root, 'No, not a person')).toBeUndefined();
		expect(await behind(root, 'No, not a person')).toBeTruthy();
		expect(await behind(root, 'Someone else')).toBeTruthy();
	});

	it('asks about a USERNAME as a username, and says a yes names nobody', async () => {
		suggestions.mockResolvedValue({
			proposals: [
				proposal({
					kind: 'username',
					proposed: 'nadiav',
					evidence: 'username_folder',
					site: 'Coomer',
					is_username: true,
					group_id: null,
					face_id: null,
					path: 'nadiav (Coomer)'
				})
			],
			total: 1
		});
		const root = await render();

		expect(question(root)).toBe('Is this the Coomer username \u2018nadiav\u2019?');
		expect(root.textContent).toContain('Yes adds no person');
		expect(root.textContent).not.toContain('a person?');
		expect(button(root, 'Yes, add to that username')).toBeTruthy();
		// The third answer names the folder as a PERSON, so on a username row it says so.
		expect(await behind(root, "No, it's one person")).toBeTruthy();

		await answer(root, 'No, not that username');
		expect(rejectSuggestion).toHaveBeenCalledWith('claim-1');
		expect(shown.mock.calls.at(-1)?.[0]).toContain('nadiav on Coomer');
		expect(shown.mock.calls.at(-1)?.[0]).toContain('not that username');
	});
});

/*
 * No bar above the cards: its sentence would repeat the lit tab's count, and the folder pass runs
 * as files settle and as every scan settles, so a re-read press would be a second door to something
 * that already happens.
 */
describe('no control of its own', () => {
	it('draws no count sentence and no re-read control', async () => {
		const root = await render();

		expect(root.textContent).not.toContain('need your input');
		expect(root.textContent).not.toContain('needs your input');
		expect(root.querySelector('button[aria-label="Read the folders again"]')).toBeNull();
	});
});

describe('what the row says', () => {
	it('marks a suggestion resting on nothing but the folder name', async () => {
		suggestions.mockResolvedValue({
			proposals: [proposal({ evidence: 'name_only', group_id: null, face_id: null })],
			total: 1
		});
		const root = await render();

		expect(root.textContent).toContain('no faces found here');
	});

	it('marks a Site folder as a Site rather than a person', async () => {
		suggestions.mockResolvedValue({
			proposals: [
				proposal({
					kind: 'site',
					proposed: 'Nightjar Media',
					evidence: 'filenames',
					per_file: ['jane doe', 'mary roe']
				})
			],
			total: 1
		});
		const root = await render();

		expect(root.querySelector('.foot .section-heading')?.textContent).toContain('a Site?');
		expect(root.textContent).not.toContain('a person?');
		// The names are tick-boxes rather than prose: a Site folder's people come one per file,
		// so leaving one out is the control the card has to offer, behind its detail line.
		button(root, 'names found in filenames')?.click();
		flushSync();
		const boxes = [...root.querySelectorAll<HTMLButtonElement>('.ticked')];
		expect(boxes).toHaveLength(2);
		expect(root.textContent).toContain('jane doe');
		expect(root.textContent).toContain('mary roe');

		boxes[1].click();
		flushSync();
		button(root, 'Yes, a Site')?.click();
		for (let turn = 0; turn < 4; turn += 1) await tick();

		// And the NAME is what is left out, because that is what the row offered.
		expect(confirmSuggestion).toHaveBeenCalledWith('claim-1', ['mary roe']);
	});

	it('warns before a merge rather than performing one', async () => {
		suggestions.mockResolvedValue({
			proposals: [proposal({ near_miss: 'person-9' })],
			total: 1
		});
		const root = await render();

		expect(root.textContent).toContain('very similar name');
	});

	it('draws the count the server gave and never a count of its own', async () => {
		const root = await render();
		expect(root.textContent).toContain('47 files');
	});

	it('says plainly when there is nothing to answer', async () => {
		suggestions.mockResolvedValue({ proposals: [], total: 0 });
		const root = await render();
		expect(root.textContent).toContain('Nothing to review');
		// What fills the list without a press, since there is no re-read control: the folder pass
		// runs as files settle and again after every scan.
		expect(root.textContent).toContain('and again after every scan');
		expect(root.textContent).not.toContain('turning arrows');
	});
});

describe('the face beside a claim', () => {
	it('is addressed with the token the row carries, so the browser may keep it', async () => {
		/* Without the token the server answers the crop the careful way, and every visit to the
		   Folders tab would re-ask about every face on the page: three 304s for three cards. */
		const root = await render();
		const picture = root.querySelector<HTMLImageElement>('.face img');
		expect(picture?.getAttribute('src')).toBe('/api/faces/face-1/crop?v=face-token');
	});

	it('becomes a blank when the server will not hand the picture over', async () => {
		/* A claim carries a face id and nothing about the vault, so this screen cannot know why a
		   crop was refused: the file behind it may be concealed, or the crop may not be there. What
		   the browser draws for a refused image is its own torn-page glyph, which reads as Sift
		   being broken; a blank claims nothing, and the Hidden mark is the server's to award. */
		const root = await render();
		const picture = root.querySelector<HTMLImageElement>('.face img');
		expect(picture).not.toBeNull();
		expect(picture?.src).toContain('/api/faces/face-1/crop');

		picture?.dispatchEvent(new Event('error'));
		flushSync();

		expect(picture?.src).toBe(BLANK_PICTURE);
		expect(picture?.src.startsWith('data:image/svg+xml')).toBe(false);
	});

	it('does not go round again when the mark itself is what failed', async () => {
		// A handler that reassigns on every failure is a loop rather than a fallback.
		const root = await render();
		const picture = root.querySelector<HTMLImageElement>('.face img');

		picture?.dispatchEvent(new Event('error'));
		flushSync();
		const first = picture?.src;
		picture?.dispatchEvent(new Event('error'));
		flushSync();

		expect(picture?.src).toBe(first);
	});
});

describe('saying who a folder really is', () => {
	it('offers a third answer beside yes and not-a-person', async () => {
		// Yes and "not a person" between them record every case the reader got right and every case
		// it was wrong to ask about. Neither says who it should have been, and that correction is
		// the only trace a MISS ever leaves.
		const root = await render();
		expect(await behind(root, 'Someone else')).toBeTruthy();
	});

	it('asks for the name, and sends nothing until there is one', async () => {
		const root = await render();

		await answer(root, 'Someone else');

		expect(root.querySelector('.correction input')).toBeTruthy();
		// Opening the box is not an answer. The answer is the name.
		expect(sayWhoAFolderIs).not.toHaveBeenCalled();
	});

	it('sends the folder and the name that was typed', async () => {
		sayWhoAFolderIs.mockResolvedValue({ files: 3, decision_id: 'd1', person_id: 'p1' });
		const root = await render();

		await answer(root, 'Someone else');
		const box = root.querySelector('.correction input') as HTMLInputElement;
		box.value = 'Somebody Else';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		for (let turn = 0; turn < 4; turn += 1) await tick();
		button(root, 'Save')?.click();
		for (let turn = 0; turn < 6; turn += 1) await tick();

		// The FOLDER, not the claim: it is a statement about the folder rather than an answer to the
		// question, and it has to work for a folder nothing ever asked about.
		expect(sayWhoAFolderIs).toHaveBeenCalledWith('folder-1', 'Somebody Else');
	});
});

describe('arriving from a still on the board', () => {
	/* A still on the Organize board's Folders card leads to the FOLDER it came from rather than to
	   the file it is a picture of: what the card is about is the folder waiting for a yes or a no.
	   A claim has no screen of its own, so the address is this page and a fragment naming the card:
	   `slices/suggestions/queue._claim_anchor` writes the other half of the name. */

	it('names every card so one of them can be pointed at', async () => {
		const root = await render();

		expect(root.querySelector('li')?.id).toBe('claim-claim-1');
	});

	it('and puts the one named in the address on screen once the rows have arrived', async () => {
		/* A fragment is scrolled to when the page is NAVIGATED to, and at that instant this screen
		   holds nothing, so the browser does nothing at all and the reveal has to happen when the
		   rows land. jsdom has no `scrollIntoView`, so this supplies one. */
		const scrolled = vi.fn();
		const had = Element.prototype.scrollIntoView;
		Element.prototype.scrollIntoView = scrolled;
		at.url = new URL('http://localhost/organize/folders#claim-claim-1');
		try {
			await render();

			expect(scrolled).toHaveBeenCalled();
		} finally {
			Element.prototype.scrollIntoView = had;
		}
	});

	it('leaves the page where it opened when the fragment names nothing here', async () => {
		/* A folder settled between the board being drawn and this page opening. The right sub-page,
		   at the top, which is the honest fallback rather than a hunt through every page for a
		   question that may no longer exist. */
		const scrolled = vi.fn();
		const had = Element.prototype.scrollIntoView;
		Element.prototype.scrollIntoView = scrolled;
		at.url = new URL('http://localhost/organize/folders#claim-gone');
		try {
			await render();

			expect(scrolled).not.toHaveBeenCalled();
		} finally {
			Element.prototype.scrollIntoView = had;
		}
	});
});

describe('the rows that leave something out', () => {
	afterEach(removeStyles);

	it("lie in a row over Pressable's block, and no other file's ticked row is touched", async () => {
		suggestions.mockResolvedValue({
			proposals: [proposal({ dissenting: ['asset-a', 'asset-b'] })],
			total: 1
		});
		const root = await render();
		button(root, 'do not match')?.click();
		flushSync();
		const row = root.querySelector('.ticked') as HTMLElement;
		applyStyles(pressable, row);
		applyStyles(source, row.closest('.odd'));

		/* The swap screens write `.ticked` on rows of their own. */
		const elsewhere = document.createElement('li');
		elsewhere.className = 'ticked';
		document.body.append(elsewhere);

		expect(getComputedStyle(row).display).toBe('flex');
		expect(getComputedStyle(elsewhere).display).not.toBe('flex');
		elsewhere.remove();
	});
});

describe('a card at its own height', () => {
	/* A folder card is a few lines of text; a wall floor would make most of them a band of empty ground. */
	it('has no floor, and draws the path and the warning whole', async () => {
		suggestions.mockResolvedValue({
			proposals: [
				proposal({
					near_miss: 'person-9',
					path: 'Models/Nadia Vance/a folder whose name runs past the end of one line'
				})
			],
			total: 1
		});
		const root = await render();
		const card = root.querySelector('.cards > li') as HTMLElement;
		const where = root.querySelector('.where') as HTMLElement;
		const why = root.querySelector('.why') as HTMLElement;
		applyStyles(source, card);

		expect(getComputedStyle(card).minBlockSize).not.toMatch(/decision-card-height/);
		for (const line of [where, why]) {
			expect(getComputedStyle(line).display).not.toBe('-webkit-box');
			expect(getComputedStyle(line).overflow).not.toBe('hidden');
		}
		expect(where.textContent).toContain('a folder whose name runs past the end of one line');
		removeStyles();
	});

	it('keeps two lines for the detail under the question, so a row of questions stands level', async () => {
		const root = await render();
		const detail = root.querySelector('.foot .detail') as HTMLElement;
		expect(detail.style.getPropertyValue('--detail-lines')).toBe('2');
	});

	it('starts the way into the detail where every other line starts', async () => {
		suggestions.mockResolvedValue({
			proposals: [proposal({ dissenting: ['asset-a', 'asset-b'] })],
			total: 1
		});
		const root = await render();
		const toggle = root.querySelector('.toggle') as HTMLElement;
		expect(toggle?.querySelector('button')).not.toBeNull();
		applyStyles(source, toggle);
		expect(getComputedStyle(toggle).display).toBe('flex');
		expect(getComputedStyle(toggle.querySelector('button') as HTMLElement).textAlign).toMatch(
			/^(inherit|start)$/
		);
		removeStyles();
	});
});
