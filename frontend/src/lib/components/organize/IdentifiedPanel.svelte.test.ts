/*
 * The people Sift knows, and what each card says about one of them.
 *
 * The card answers three questions in one reading and in this order: what you confirmed, what Sift
 * named on its own, and what needs your input; a card with nothing waiting says so in the third
 * one's own words.
 *
 * The three phrases are the same on every screen: confirmed, recognized by Sift, needs your
 * input. What a beginner needs is whether Sift is asking them anything, which only the third is.
 *
 * The two acts are two questions, and each appears only when there is something of its kind to
 * answer: an offer to agree with what Sift named, on a card where it named nothing, would be a row
 * that does nothing.
 *
 * One control rather than two buttons, which would not fit a card fourteen rems wide. Which act
 * leads is decided by state: the answer leads whenever there is one to give, however few, and the
 * nod leads only where nothing is asked.
 *
 * The control must fit the card. Two things at the foot of this file hold that: the words on the
 * lead half are short, and the card is the width measured for a card with a row of controls.
 */
import { readFileSync } from 'node:fs';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

import { words as readable } from '$lib/design/testing.svelte';

const identifiedPeople = vi.fn();
const confirmMatches = vi.fn();
const confirmLookAlikes = vi.fn();
const rejectLookAlikes = vi.fn();
const rejectMatches = vi.fn();
const referenceStrengths = vi.fn();

vi.mock('$lib/people/faces.svelte', () => ({
	CROPS_ON_A_CARD: 12,
	IDENTIFIED_PEOPLE_PER_PAGE: 24,
	identifiedPeople: (...a: unknown[]) => identifiedPeople(...a),
	confirmMatches: (...a: unknown[]) => confirmMatches(...a),
	confirmLookAlikes: (...a: unknown[]) => confirmLookAlikes(...a),
	rejectLookAlikes: (...a: unknown[]) => rejectLookAlikes(...a),
	rejectMatches: (...a: unknown[]) => rejectMatches(...a),
	referenceStrengths: (...a: unknown[]) => referenceStrengths(...a),
	recognitionOf: vi.fn(),
	referenceVerdict: () => 'good',
	cropUrl: (id: string) => `/api/faces/${id}/crop`,
	faceCoverUrl: (id: string) => `/api/faces/${id}/cover`,
	blankOnRefusal: () => {}
}));

const said = vi.hoisted(() => [] as unknown[]);
vi.mock('$lib/shell/toasts.svelte', () => ({
	toasts: { show: (words: unknown) => said.push(words) }
}));
/* `replaceState` as well as `goto`: the wall writes its own anchor into the address as it settles,
   and a mock naming only what this file asserts takes that away and fails the load. */
const goto = vi.fn();
vi.mock('$app/navigation', () => ({
	goto: (...a: unknown[]) => goto(...a),
	replaceState: () => {}
}));
vi.mock('$lib/library/changes.svelte', async () => ({
	...(await vi.importActual<typeof import('$lib/library/changes.svelte')>(
		'$lib/library/changes.svelte'
	)),
	reloadOnLibraryChange: () => {}
}));

import IdentifiedPanel from './IdentifiedPanel.svelte';
import { rematching } from '$lib/components/faces/WaitingForYou.svelte';

/* Invented for this file, as the rule for a fixture in this repo asks. */
function card(over: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		person_id: 'person-1',
		person_name: 'Wren Halloway',
		size: 370,
		waiting: 13,
		matched: 344,
		confirmed: 13,
		surest: 0.91,
		faces: [{ track_id: 'face-a', asset_id: 'asset-1', started_ms: 0, ended_ms: 0 }],
		...over
	};
}

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	referenceStrengths.mockResolvedValue({
		people: { 'person-1': 13 },
		verdicts: { 'person-1': 'good' },
		target: 20,
		floor: 5,
		strong: 10
	});
	confirmMatches.mockResolvedValue({ confirmed: 344, references: 344 });
	confirmLookAlikes.mockResolvedValue({ changed: 13, offered: 0, person_id: 'person-1' });
});

afterEach(() => {
	rematching.people.clear();
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
	// The menu is portalled to the end of the document, so removing the host leaves it behind.
	document.body.innerHTML = '';
});

async function render(person: Record<string, unknown>): Promise<HTMLElement> {
	identifiedPeople.mockResolvedValue({ people: [person], total: 1, offset: 0 });
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(IdentifiedPanel, { target: host }) as Record<string, unknown>;
	flushSync();
	for (let turn = 0; turn < 5; turn += 1) await tick();
	flushSync();
	return host;
}

function press(root: HTMLElement, words: string): HTMLButtonElement | undefined {
	return [...root.querySelectorAll<HTMLButtonElement>('button')].find((one) =>
		one.textContent?.includes(words)
	);
}

/** The lead half: the one button on the card that carries words. */
function lead(root: HTMLElement): string {
	const half = root.querySelector<HTMLButtonElement>('.row .lead button');
	if (!half) throw new Error('the card has no lead half');
	return readable(half);
}

/* The library opens on POINTERDOWN, not on click: sending both is one press that opens and
   closes again, which reads in an assertion as a door that never opened. */
function openMenu(root: HTMLElement) {
	const door = root.querySelector<HTMLButtonElement>('.row .trail button');
	if (!door) throw new Error('the card has no menu half');
	door.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
	flushSync();
}

/** What is behind the chevron, in the order it is drawn. */
function menuRows(): string[] {
	return [...document.querySelectorAll('.ui-menu [role="menuitem"]')].map((one) =>
		readable(one as HTMLElement)
	);
}

function menuRow(label: string): HTMLElement {
	const found = [...document.querySelectorAll('.ui-menu [role="menuitem"]')].find(
		(one) => readable(one as HTMLElement) === label
	);
	if (!found) throw new Error(`no row reading ${label}`);
	return found as HTMLElement;
}

it('says the three numbers in the words every faces screen says them in', async () => {
	const root = await render(card());

	const said = [...root.querySelectorAll('.one')].map((one) => one.textContent?.trim());
	expect(said).toEqual([
		'You confirmed 13 as Wren',
		'Sift recognized 344 as Wren',
		'13 awaiting your input'
	]);
});

it('says the one that is waiting in the singular when there is one of it', async () => {
	/* A card one press from empty is the most-looked-at state this wall has, and "1 need your
	   answer" is the easy fault for a count. */
	const root = await render(card({ waiting: 1 }));

	const said = [...root.querySelectorAll('.one')].map((one) => one.textContent?.trim());
	expect(said).toContain('1 awaiting your input');
	expect(said).not.toContain('1 awaiting your inputs');
});

it('says nothing needs an answer rather than that the card looks good', async () => {
	/* "Looks good" would be the card grading itself. What somebody wants off a card with nothing on it
	   is what is LEFT, which is nothing, said in the same words the number beside it uses. */
	const root = await render(card({ waiting: 0 }));

	expect(root.textContent).toContain('Nothing needs your input');
	expect(root.textContent).not.toContain('Looks good');
});

it('leads with the answer whenever there is one to give, however few', async () => {
	/*
	 * The lead is chosen by state, not by the act with more faces behind it: thirteen questions
	 * beside 344 faces Sift has already named is still the card asking thirteen questions.
	 */
	const fewWaiting = await render(card({ matched: 344, waiting: 13 }));
	expect(lead(fewWaiting)).toBe('Yes (13)');

	unmount(mounted as Record<string, unknown>);
	mounted = null;
	host.remove();

	const manyWaiting = await render(card({ matched: 344, waiting: 2444 }));
	expect(lead(manyWaiting)).toBe('Yes (2,444)');
});

it('answers a question with a yes rather than with a count and a neutral verb', async () => {
	/*
	 * The lead is a yes, not "Answer N": the count line already says how many need an answer, and
	 * the press's outcome is agreement, so its word says so.
	 */
	const root = await render(card({ matched: 344, waiting: 13 }));

	expect(lead(root)).toBe('Yes (13)');
	expect(root.textContent).not.toContain('Answer 13');
});

it('wears ONE shape on the lead whichever of the two acts it is about', async () => {
	/*
	 * One label shape, Yes (N), whichever act leads: a wall of cards is read one word and one
	 * number at a time, and a label rewording itself with the state would make somebody re-read the
	 * button before every press. The number is what the press settles; the counts above say the
	 * state.
	 */
	const asking = await render(card({ matched: 344, waiting: 13 }));
	expect(lead(asking)).toBe('Yes (13)');

	unmount(mounted as Record<string, unknown>);
	mounted = null;
	host.remove();
	document.body.innerHTML = '';

	const settled = await render(card({ matched: 19, waiting: 0 }));
	expect(lead(settled)).toBe('Yes (19)');
	expect(settled.textContent).not.toContain('Sift is right');
});

it('puts a no that WRITES in the menu, over exactly what the yes confirms', async () => {
	/*
	 * The refusal writes, rather than only opening the screen: each door writes a record History
	 * can take back, which is what makes a bulk refusal safe on a card.
	 */
	const root = await render(card({ matched: 344, waiting: 13 }));

	openMenu(root);
	menuRow('No (13)').click();
	flushSync();
	for (let turn = 0; turn < 4; turn += 1) await tick();

	expect(rejectLookAlikes).toHaveBeenCalledWith('person-1');
});

it('refuses the matches where the matches are what the yes confirms', async () => {
	/*
	 * The two halves of one question: the menu row follows the lead rather than the state it
	 * happens to find, since a card leading with the nod has nothing waiting to refuse.
	 */
	const root = await render(card({ matched: 19, waiting: 0 }));

	openMenu(root);
	menuRow('No (19)').click();
	flushSync();
	for (let turn = 0; turn < 4; turn += 1) await tick();

	expect(rejectMatches).toHaveBeenCalledWith('person-1');
});

it('keeps the row that opens them one at a time, for somebody who wants to look', async () => {
	const root = await render(card({ matched: 344, waiting: 13 }));

	openMenu(root);
	menuRow('No, one at a time').click();
	flushSync();
	await tick();

	expect(goto).toHaveBeenCalledWith('/organize/known-people/person-1?show=suggested');
});

it('draws the same three rows behind the chevron whatever the card is about', async () => {
	/*
	 * One shape, so the menu is learned once. The act that did not lead is not a row here: it lives
	 * on that person's own screen, on the tab holding those faces, where what is being agreed to is
	 * on the page.
	 */
	const asking = await render(card({ matched: 344, waiting: 2444 }));

	openMenu(asking);
	expect(menuRows()).toEqual(['No (2,444)', 'No, one at a time', 'Show me']);

	unmount(mounted as Record<string, unknown>);
	mounted = null;
	host.remove();
	document.body.innerHTML = '';

	const settled = await render(card({ matched: 344, waiting: 0 }));
	openMenu(settled);
	expect(menuRows()).toEqual(['No (344)', 'No, one at a time', 'Show me']);
});

it('leads with the nod where nothing is being asked at all', async () => {
	const without = await render(card({ matched: 344, waiting: 0 }));

	expect(lead(without)).toBe('Yes (344)');
});

it('draws no control at all on a card with nothing to do', async () => {
	/* Neither act has anything behind it, and a control that can do nothing is one somebody has to
	   press to find that out. The card is then the three numbers and the name. */
	const root = await render(card({ matched: 0, waiting: 0 }));

	expect(root.querySelector('.row')).toBeNull();
	expect(root.textContent).toContain('Nothing needs your input');
});

it('agrees with every match for the person rather than with the faces on the card', async () => {
	/* A card draws a handful of crops of however many there are, so a press built from its own list
	   would settle the handful and leave the rest, with the card then saying a smaller number and
	   nothing saying why. The person is what the server is asked about.

	   Nothing waiting, so the nod is the half under the pointer: with a question standing it is
	   behind the chevron instead, which the test below presses it from. */
	const root = await render(card({ matched: 344, waiting: 0 }));

	press(root, 'Yes (344)')?.click();
	flushSync();
	for (let turn = 0; turn < 4; turn += 1) await tick();

	expect(confirmMatches).toHaveBeenCalledWith('person-1');
	expect(rematching.people.has('person-1')).toBe(true);
});

it('agrees with every proposal for the person from the answer', async () => {
	const root = await render(card({ matched: 344, waiting: 2444 }));

	root.querySelector<HTMLButtonElement>('.row .lead button')?.click();
	flushSync();
	for (let turn = 0; turn < 4; turn += 1) await tick();

	expect(confirmLookAlikes).toHaveBeenCalledWith('person-1');
});

it('marks her card while the re-match a Yes asked for runs', async () => {
	const root = await render(card({ matched: 344, waiting: 2444 }));
	expect(root.querySelector('.who [role="status"]')).toBeNull();

	root.querySelector<HTMLButtonElement>('.row .lead button')?.click();
	flushSync();
	for (let turn = 0; turn < 4; turn += 1) await tick();
	flushSync();

	expect(root.querySelector('.who [role="status"]')?.getAttribute('aria-label')).toBe(
		"Sift is matching the rest of the library against Wren's face"
	);
	// The agreeing is a task: the press says what it handed over.
	expect(said).toContain('Agreeing with 13 faces');
});

it('marks everybody a Yes over a selection was about', async () => {
	const root = await render(card({ matched: 344, waiting: 2444 }));
	root
		.querySelector('.person a')
		?.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, ctrlKey: true }));
	flushSync();
	for (let turn = 0; turn < 2; turn += 1) await tick();

	const bar = document.querySelector<HTMLElement>('[role="region"][aria-label="Selection"]');
	expect(bar).not.toBeNull();
	press(bar as HTMLElement, 'Yes (2,444)')?.click();
	for (let turn = 0; turn < 6; turn += 1) await tick();

	expect(confirmLookAlikes).toHaveBeenCalledWith('person-1');
	expect(rematching.people.has('person-1')).toBe(true);
});

it('hands a press on the card ground to the faces it opens', async () => {
	const root = await render(card({ matched: 344, waiting: 2444 }));
	const person = root.querySelector('li.person') as HTMLElement;
	const link = person.querySelector(
		'a[href="/organize/known-people/person-1?show=suggested"]'
	) as HTMLAnchorElement;
	const opened = vi.fn((event: Event) => event.preventDefault());
	link.addEventListener('click', opened);

	person
		.querySelector('.counts')
		?.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));

	expect(opened).toHaveBeenCalledTimes(1);
	expect(person.classList.contains('opens')).toBe(true);
});

it('asks the PERSON from the menu as well as from the lead half', async () => {
	/* Every press here is asked of the person, never of the card's own list: a card draws a handful
	   of crops of however many there are, so a press built from that list would settle the handful
	   and leave the rest, with the card then saying a smaller number and nothing saying why. */
	const matchesLead = await render(card({ matched: 344, waiting: 2444 }));

	openMenu(matchesLead);
	menuRow('No (2,444)').click();
	flushSync();
	for (let turn = 0; turn < 4; turn += 1) await tick();

	expect(rejectLookAlikes).toHaveBeenCalledWith('person-1');

	unmount(mounted as Record<string, unknown>);
	mounted = null;
	host.remove();
	document.body.innerHTML = '';

	const waitingLead = await render(card({ matched: 344, waiting: 13 }));

	waitingLead.querySelector<HTMLButtonElement>('.row .lead button')?.click();
	flushSync();
	for (let turn = 0; turn < 4; turn += 1) await tick();

	expect(confirmLookAlikes).toHaveBeenCalledWith('person-1');
});

it('sends Show me to the faces the thumbnails open, on the tab the card is about', async () => {
	/*
	 * One way to this person's faces rather than two: the same address the crops above carry, and
	 * it names the tab, because the screen it opens defaults to the faces needing an answer and a
	 * card leading with the nod has none.
	 */
	const root = await render(card());

	openMenu(root);
	menuRow('Show me').click();
	flushSync();
	await tick();

	expect(goto).toHaveBeenCalledWith('/organize/known-people/person-1?show=suggested');
});

it('opens the attributed faces from a card whose act is about them', async () => {
	/* The other half of the same rule: nothing waiting, six attributed, so the tab with something
	   on it is the attributed one. */
	const root = await render(card({ matched: 6, waiting: 0 }));

	const crops = root.querySelector('a[href^="/organize/known-people/"]');
	expect(crops?.getAttribute('href')).toBe('/organize/known-people/person-1?show=matched');
});

it('narrows itself not at all by how a face was named', async () => {
	/*
	 * No row of tabs on this wall: every card already carries both numbers, so filtering would cut
	 * the list to cards holding a figure none of them hides. The split worth having is on one
	 * person, which is what her own screen's tabs are. The one narrowing is by what Sift knows a
	 * person from, the control on the tab line (`IdentifiedPanel.starters.test.ts`).
	 */
	const root = await render(card());

	expect(root.querySelector('.narrowing')).toBe(null);
	// And the read asks for everybody: a filter sent from here would be a filter with no
	// control over it, which is the worst of both.
	expect(identifiedPeople).toHaveBeenCalledWith(expect.objectContaining({ limit: 24 }), null, '');
});

/*
 * The control fits the card, at the narrowest the wall can draw one.
 *
 * jsdom lays nothing out, so the box is measured in a real engine. What is pinned here is what that
 * measurement depends on, each one word from being undone: the card's width, and the length of the
 * words on the half that must not fold.
 *
 * At 13.5px in this typeface the lead half is its padding (2 x 16), its tick (16), the gap after it
 * (8) and its words, and the chevron is a square of the control height (36); a 15.25rem card leaves
 * 220px after its inset. The lead is "Yes" on every card that asks anything. The nod, "Yes, Sift is
 * right (344)", leads only where nothing is asked and is the long one; it may wrap rather than
 * spill (see `SplitButton`). What is pinned is the card's width and that the refused wording has
 * not come back.
 */
describe('the one control fits the narrowest card this wall draws', () => {
	const source = readFileSync('src/lib/components/organize/IdentifiedPanel.svelte', 'utf8');

	it('draws its cards at the width the groups wall was measured for', () => {
		// The same column `FaceGroups` uses. Two walls of one feature at two widths is how a control
		// that fits one comes to overflow the other.
		expect(source).toContain('minmax(15.25rem, 1fr)');
	});

	it('keeps the refused words off the card', () => {
		// The three forms a removed wording could come back as: the lead half's own words, the menu
		// row naming the same act with its count, and a phrase too long for the card. Literals
		// rather than a shape, because each is a specific wording.
		expect(source).not.toContain("'These matches are right'");
		expect(source).not.toContain('`These matches are right');
		expect(source).not.toContain("'Matches are right'");
		expect(source).not.toContain("'Confirm all'");
	});
});

describe('a person the vault conceals', () => {
	it('says Hidden on the card rather than Not named', async () => {
		/*
		 * The server withholds the name and the id together and gathers everybody concealed under one
		 * nameless card, so a card with no id on this wall can only be that gather: every face here
		 * is already attached to somebody, and a face nobody has named is a question on the other tab.
		 * "Not named" would be the opposite state: somebody Sift found and nobody has named.
		 * "Hidden" is the word the vault uses on a tile and in the band.
		 */
		const root = await render(card({ person_id: null, person_name: null }));

		expect(root.querySelector('.who')?.textContent?.trim()).toBe('Hidden');
		expect(root.textContent).not.toContain('Not named');
	});
});
