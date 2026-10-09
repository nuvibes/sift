/*
 * The wall of unnamed piles, and two things held about it.
 *
 * Naming must work when the person is not in the library yet: the picker's create row makes them
 * here, without a trip to People. And the wall has a selection: the long-press-then-click gesture
 * that works on every other wall of tiles picks a pile here rather than opening it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount, tick } from 'svelte';

import FaceGroups from './FaceGroups.svelte';
import source from './FaceGroups.svelte?raw';
import entityCard from '$lib/components/entity/EntityCard.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import type { PileStatus } from '$lib/people/faces.svelte';
import { noServerAt } from '../../../test-setup';

/* Left unanswered on purpose: the card art and the interface settings, read and kept on the way past. */
noServerAt('/api/creator-art', '/api/settings/interface');

const faceGroups = vi.fn();
const nameFaces = vi.fn();
const ignoreGroup = vi.fn();
const restoreGroup = vi.fn();
const confirmFace = vi.fn();
const referenceStrengths = vi.fn();
const fingerprintOffers = vi.fn();
const makePersonFromFingerprints = vi.fn();
const goneTo = vi.fn();

/* The sentence stays the module's own, so the card is checked against the words it really says. */
vi.mock('$lib/people/fingerprint-offers', async (importActual) => ({
	...(await importActual<typeof import('$lib/people/fingerprint-offers')>()),
	fingerprintOffers: (...a: unknown[]) => fingerprintOffers(...a),
	makePersonFromFingerprints: (...a: unknown[]) => makePersonFromFingerprints(...a)
}));

vi.mock('$app/navigation', () => ({
	goto: (to: string) => goneTo(to),
	pushState: vi.fn(),
	replaceState: vi.fn(),
	invalidateAll: vi.fn(async () => {}),
	beforeNavigate: vi.fn(),
	afterNavigate: vi.fn()
}));

vi.mock('$lib/people/faces.svelte', () => ({
	CROPS_ON_A_CARD: 12,
	PILES_PER_PAGE: 24,
	faceGroups: (...a: unknown[]) => faceGroups(...a),
	nameFaces: (...a: unknown[]) => nameFaces(...a),
	ignoreGroup: (...a: unknown[]) => ignoreGroup(...a),
	restoreGroup: (...a: unknown[]) => restoreGroup(...a),
	confirmFace: (...a: unknown[]) => confirmFace(...a),
	referenceStrengths: (...a: unknown[]) => referenceStrengths(...a),
	regroupFaces: vi.fn(),
	cropUrl: (id: string) => `/api/faces/${id}/crop`
}));

/* Invented for this file. Nobody real, which is the rule for a fixture in this repo. */
const EVERYBODY = [{ id: 'person-1', name: 'Ada Lovelace' }];
const madePerson = vi.fn(async (name: string) => ({ id: 'person-new', name }));

vi.mock('$lib/people/people.svelte', () => ({
	people: {
		get items() {
			return EVERYBODY;
		},
		loaded: true,
		loading: false,
		/* One PAGE of people for the picker, filtered by what is typed, with the count of what did
		   not fit: the server's own answer, because the wall's cached page is not what a name is
		   looked up in. */
		choices: async (typed: string) => {
			const needle = (typed ?? '').trim().toLowerCase();
			const items = EVERYBODY.filter((one) => one.name.toLowerCase().includes(needle));
			return { items, total: items.length };
		},
		create: (name: string) => madePerson(name)
	}
}));

const shown = vi.fn();
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: (t: string) => shown(t) } }));
/* The picker asks for the live names of its remembered rows; none here. */
vi.mock('$lib/entity/names-now.svelte', () => ({
	askNames: vi.fn(async () => undefined),
	nameNow: () => undefined
}));
vi.mock('$lib/library/changes.svelte', async () => ({
	...(await vi.importActual<typeof import('$lib/library/changes.svelte')>(
		'$lib/library/changes.svelte'
	)),
	reloadOnLibraryChange: () => {}
}));

function group(id: string) {
	return {
		id,
		status: 'open',
		size: 3,
		faces: [
			{ track_id: `${id}-a`, asset_id: 'asset-1', started_ms: 0, ended_ms: 0 },
			{ track_id: `${id}-b`, asset_id: 'asset-2', started_ms: 0, ended_ms: 0 }
		]
	};
}

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	faceGroups.mockResolvedValue({ groups: [group('pile-1'), group('pile-2')], total: 2 });
	referenceStrengths.mockResolvedValue({ people: {}, target: 20, floor: 5 });
	nameFaces.mockResolvedValue({ changed: 2, person_id: 'person-new' });
	ignoreGroup.mockResolvedValue(undefined);
	restoreGroup.mockResolvedValue(undefined);
	fingerprintOffers.mockResolvedValue(new Map());
	makePersonFromFingerprints.mockResolvedValue('person-made');
});

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
});

async function render(status: PileStatus = 'open'): Promise<HTMLElement> {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(FaceGroups, { target: host, props: { status } }) as Record<string, unknown>;
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

/*
 * A verb the bar keeps behind its three dots, opened and read.
 *
 * The bar names a few verbs and puts the rest one press away: here "Add as person" and Delete keep
 * their words and Discard and Restore are behind the door. Asking the bar for a button would find
 * nothing, which reads like the verb having been dropped.
 *
 * The rows are portalled to the end of the document, so they are looked for there. Disabled is read
 * off `aria-disabled`, because a menu row is not a `<button>`: bits-ui keeps a disabled row in the
 * list, announced and unreachable, the same "offered and does not apply" the bar's buttons show.
 */
async function behindTheDoor(root: HTMLElement, words: string): Promise<HTMLElement | undefined> {
	const door = [...root.querySelectorAll<HTMLButtonElement>('button')].find((one) =>
		one.className.includes('more')
	);
	door?.click();
	flushSync();
	for (let turn = 0; turn < 4; turn += 1) await tick();
	return [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((row) =>
		(row.textContent ?? '').replace(/[\uE000-\uF8FF]/g, '').includes(words)
	);
}

function cards(root: HTMLElement): HTMLElement[] {
	return [...root.querySelectorAll<HTMLElement>('a.faces')];
}

/** ctrl-click, which is what picking is while nothing is selected yet. */
function pick(one: HTMLElement) {
	one.dispatchEvent(new MouseEvent('click', { bubbles: true, ctrlKey: true }));
	flushSync();
}

/*
 * Naming a pile, which is the SAME selector the "Add to" flyouts are.
 *
 * A box, suggestions, an "Add as a new person" row: every one of those is something `PickMenu`
 * already does. So what is asserted here is what this SCREEN is responsible for: that the picker is
 * behind the card's own button, that picking somebody writes the whole pile in one call, and that
 * creating somebody makes them and then names the pile as them. How the list itself behaves is
 * `PickMenu`'s own suite and is deliberately not restated here.
 *
 * The list is PORTALLED to the end of the document by the door that opens it, so the rows are found
 * in `document` and not inside the card.
 */
describe('naming a pile', () => {
	/** Open the picker behind a card's own "Add as person", and let its first page land. */
	async function openTheList(root: HTMLElement): Promise<void> {
		button(root, 'Add as person')?.click();
		flushSync();
		for (let turn = 0; turn < 4; turn += 1) await tick();
		flushSync();
	}

	/**
	 * Every row of the flyout. The glyph is a ligature, so its codepoint is stripped.
	 *
	 * What a row says is its name, read off `.name` rather than off the row: rows carry the
	 * person's picture, and a person with no cover draws a monogram letter inside the row's text
	 * ("A Ada Lovelace"). The create row has no `.name`, so it falls back to the row.
	 */
	function saying(row: Element): string {
		const name = row.querySelector('.name');
		return ((name ?? row).textContent ?? '').replace(/[\uE000-\uF8FF]/g, '').trim();
	}

	function rows(): string[] {
		return [
			...document.querySelectorAll('.pick [role="menuitem"], .pick [role="menuitemcheckbox"]')
		].map(saying);
	}

	function press(words: string): void {
		const row = [
			...document.querySelectorAll('.pick [role="menuitem"], .pick [role="menuitemcheckbox"]')
		].find((one) => saying(one) === words);
		if (!(row instanceof HTMLElement)) throw new Error(`there is no row saying ${words}`);
		row.click();
		flushSync();
	}

	/** Type into the picker's box and wait out its debounce. */
	async function type(text: string): Promise<void> {
		const input = document.querySelector('.pick input');
		if (!(input instanceof HTMLInputElement)) throw new Error('there is no box to narrow with');
		input.focus();
		input.value = text;
		input.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await new Promise((resolve) => setTimeout(resolve, 200));
		for (let turn = 0; turn < 4; turn += 1) await tick();
		flushSync();
	}

	it('names the whole pile as somebody who already exists, in ONE write', async () => {
		const root = await render();
		await openTheList(root);

		expect(rows()).toContain('Ada Lovelace');
		press('Ada Lovelace');
		for (let turn = 0; turn < 4; turn += 1) await tick();

		/* One call for the pile, not one per face, and the third argument is "and the rest of this
		   group too". A card draws a PAGE of a pile, so without it a group of forty would be half
		   named with nothing on screen to say so, and only the server knows the whole pile. */
		expect(nameFaces).toHaveBeenCalledWith(
			['pile-1-a', 'pile-1-b'],
			{ personId: 'person-1' },
			true
		);
		expect(nameFaces).toHaveBeenCalledTimes(1);
	});

	it('offers to add somebody the library does not have yet, and names the pile as them', async () => {
		const root = await render();
		await openTheList(root);
		await type('Nadia Vance');

		/* The create row is offered only because nobody on the page is called that: otherwise the
		   quick way to a second "Ada Lovelace" would be sitting under the first one. */
		expect(rows()).toContain('Create Nadia Vance');
		press('Create Nadia Vance');
		for (let turn = 0; turn < 6; turn += 1) await tick();

		expect(madePerson).toHaveBeenCalledWith('Nadia Vance');
		expect(nameFaces).toHaveBeenCalledWith(
			['pile-1-a', 'pile-1-b'],
			{ personId: 'person-new' },
			true
		);
	});

	it('offers no way to create somebody already on the page', async () => {
		const root = await render();
		await openTheList(root);
		await type('Ada Lovelace');

		expect(rows()).toEqual(['Ada Lovelace']);
	});
});

describe('picking several piles', () => {
	it('lets go of one that is picked when it is clicked again', async () => {
		const root = await render();
		const first = cards(root)[0];

		pick(first);
		expect(root.querySelectorAll('a.faces.picked')).toHaveLength(1);

		first.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		// A second press on something picked lets go of it rather than opening it; the card is a
		// link, so the click has to be stopped as well.
		//
		// Read off the card rather than the bar: the bar animates away, staying in the document for
		// a beat after the selection empties, so reading its text would test the timing rather than
		// the selection.
		expect(root.querySelectorAll('a.faces.picked')).toHaveLength(0);
	});

	it('ignores everything picked in one press', async () => {
		const root = await render();
		pick(cards(root)[0]);
		pick(cards(root)[1]);
		expect(root.textContent).toContain('2 groups');

		(await behindTheDoor(root, 'Discard them'))?.click();
		for (let turn = 0; turn < 6; turn += 1) await tick();

		expect(ignoreGroup.mock.calls.map(([id]) => id)).toEqual(['pile-1', 'pile-2']);
	});

	it('brings them back instead, on the wall of what was set aside', async () => {
		const root = await render('ignored');
		pick(cards(root)[0]);

		/*
		 * One bar, the same on both walls, with what does not apply disabled rather than absent: a
		 * missing control reads as a feature that does not exist, a greyed one as one that does not
		 * apply. Both are behind the bar's three dots, and both are still offered there.
		 */
		expect(await behindTheDoor(root, 'Discard them')).toBeDefined();
		expect((await behindTheDoor(root, 'Discard them'))?.getAttribute('aria-disabled')).toBe('true');
		expect((await behindTheDoor(root, 'Restore'))?.getAttribute('aria-disabled')).not.toBe('true');

		(await behindTheDoor(root, 'Restore'))?.click();
		for (let turn = 0; turn < 6; turn += 1) await tick();

		expect(restoreGroup.mock.calls.map(([id]) => id)).toEqual(['pile-1']);
	});

	it('greys the same bar the other way round on the wall of what is waiting', async () => {
		const root = await render('open');
		pick(cards(root)[0]);

		expect((await behindTheDoor(root, 'Discard them'))?.getAttribute('aria-disabled')).not.toBe(
			'true'
		);
		expect((await behindTheDoor(root, 'Restore'))?.getAttribute('aria-disabled')).toBe('true');
		// Delete destroys something, so it is one of the ones the bar keeps NAMED.
		expect(button(root, 'Delete')).toBeDefined();
	});
});

describe('a press on a card', () => {
	/* Anywhere on a card that is not a control opens the group, handed to the card's own link, so
	   whatever a press on the faces does (open, or pick while picking) the ground does too. */
	it('hands a press on the ground to the link that opens the group', async () => {
		const root = await render();
		const card = root.querySelector('.wall > li') as HTMLElement;
		const link = card.querySelector('a[href^="/organize/"]') as HTMLAnchorElement;
		const opened = vi.fn((event: Event) => event.preventDefault());
		link.addEventListener('click', opened);

		card
			.querySelector('.foot .detail')
			?.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));

		expect(opened).toHaveBeenCalledTimes(1);
		expect(card.querySelector('.card.opens')).not.toBeNull();
	});
});

describe('the question on a card', () => {
	/* The one shape every Organize question wears: the crops, then the question with the line
	   the board says under it, then the answers, naming as the button and the rarer two behind
	   its chevron in the declared words. A discarded group is asked nothing and offers the way
	   back. */
	it('asks who the group is, with naming as the button and the rest behind it', async () => {
		const root = await render();
		const card = root.querySelector('.wall > li') as HTMLElement;

		expect(card.querySelector('.foot .section-heading')?.textContent?.trim()).toBe('Who is this?');
		expect(card.querySelector('.foot .detail')?.textContent).toBe('3 faces look like one person');
		expect(card.querySelector('.foot .split .lead button')?.textContent).toContain('Add as person');

		card
			.querySelector('.foot .split .trail button')
			?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
		flushSync();
		for (let turn = 0; turn < 2; turn += 1) await tick();
		const rows = [...document.querySelectorAll('[role="menuitem"]')].map(
			(row) => row.textContent ?? ''
		);
		expect(rows.some((row) => row.includes('Discard'))).toBe(true);
		expect(rows.some((row) => row.includes('Delete'))).toBe(true);
	});

	it('asks a discarded group nothing, and offers it back', async () => {
		const root = await render('ignored');
		const card = root.querySelector('.wall > li') as HTMLElement;

		expect(card.querySelector('.foot .section-heading')).toBeNull();
		expect(card.querySelector('.foot .detail')?.textContent).toBe('3 faces');
		expect(card.querySelector('.foot button')?.textContent).toContain('Restore');
	});
});

/*
 * A group that looks like somebody a facial fingerprints file holds, while making people from
 * fingerprints is off. The server answers which groups (faked here: the answer is keyed by the
 * group); the card asks whether to make them a person, one press makes them, and then the card
 * says who was made as a link to them. A group the server names no one for asks as before.
 */
describe('the fingerprints question on a card', () => {
	const offer = { entry_id: 'entry-7', name: 'Liora Fenwick', pile_id: 'pile-2' };

	it('asks to make them a person on the group that looks like them, and on no other', async () => {
		fingerprintOffers.mockResolvedValue(new Map([['pile-2', offer]]));
		const root = await render();
		const [first, second] = [...root.querySelectorAll<HTMLElement>('.wall > li')];

		expect(first.querySelector('.foot .section-heading')?.textContent?.trim()).toBe('Who is this?');
		expect(second.querySelector('.foot .section-heading')?.textContent?.trim()).toBe(
			'This group looks like Liora Fenwick, from a facial fingerprints file. Create a person for them?'
		);
		expect(second.querySelector('.foot .split .lead button')?.textContent).toContain(
			'Create a person'
		);
	});

	it('makes the person in one press, then links to them', async () => {
		fingerprintOffers.mockResolvedValue(new Map([['pile-2', offer]]));
		const root = await render();
		const second = root.querySelectorAll<HTMLElement>('.wall > li')[1];
		second.querySelector<HTMLButtonElement>('.foot .split .lead button')?.click();
		await vi.waitFor(() => expect(makePersonFromFingerprints).toHaveBeenCalledWith('entry-7'), {
			interval: 1
		});
		await vi.waitFor(
			() => expect(second.querySelector('.foot .section-heading a')).not.toBeNull(),
			{ interval: 1 }
		);

		const link = second.querySelector<HTMLAnchorElement>('.foot .section-heading a');
		expect(link?.textContent).toBe('Liora Fenwick');
		expect(link?.getAttribute('href')).toBe('/people/person-made');
		expect(
			second.querySelector('.foot .section-heading')?.textContent?.replace(/\s+/g, ' ').trim()
		).toBe('Liora Fenwick is a person now');
		expect(second.querySelector('.foot button')).toBeNull();
		expect(nameFaces).not.toHaveBeenCalled();
	});

	it('keeps naming the group as somebody else one press away, on its own page', async () => {
		fingerprintOffers.mockResolvedValue(new Map([['pile-2', offer]]));
		const root = await render();
		const second = root.querySelectorAll<HTMLElement>('.wall > li')[1];
		second
			.querySelector('.foot .split .trail button')
			?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
		flushSync();
		for (let turn = 0; turn < 2; turn += 1) await tick();
		const row = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((one) =>
			one.textContent?.includes('Open this group')
		);
		expect(row).toBeDefined();
		row?.click();
		await vi.waitFor(() => expect(goneTo).toHaveBeenCalled(), { interval: 1 });
		expect(String(goneTo.mock.calls[0][0])).toContain('pile-2');
	});

	it('asks a discarded group nothing about fingerprints', async () => {
		fingerprintOffers.mockResolvedValue(new Map([['pile-1', { ...offer, pile_id: 'pile-1' }]]));
		const root = await render('ignored');
		expect(root.textContent).not.toContain('fingerprints');
		expect(fingerprintOffers).not.toHaveBeenCalled();
	});
});

describe("a card's menu trigger", () => {
	afterEach(removeStyles);

	it("takes no box by this file's rule, and a person card's rule does not reach it", async () => {
		const trigger = (await render()).querySelector('[data-context-menu-trigger]') as HTMLElement;

		applyStyles(entityCard);
		expect(getComputedStyle(trigger).display).not.toBe('contents');

		applyStyles(source);
		expect(getComputedStyle(trigger).display).toBe('contents');
	});
});
