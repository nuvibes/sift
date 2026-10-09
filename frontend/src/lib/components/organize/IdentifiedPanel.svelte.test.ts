/* The people Sift knows, and each card's one reading: confirmed, recognized by Sift, needs your
 * input. Each act shows only when it has something to answer; one control leads by state and fits.
 * */
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
/* `replaceState` too: the wall writes its anchor as it settles. */
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

/* The library opens on pointerdown; a click too would close it again. */
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
	/* "1 need your answer" is the easy fault for a count. */
	const root = await render(card({ waiting: 1 }));

	const said = [...root.querySelectorAll('.one')].map((one) => one.textContent?.trim());
	expect(said).toContain('1 awaiting your input');
	expect(said).not.toContain('1 awaiting your inputs');
});

it('says nothing needs an answer rather than that the card looks good', async () => {
	/* What is left, in the count's own words, not a grade. */
	const root = await render(card({ waiting: 0 }));

	expect(root.textContent).toContain('Nothing needs your input');
	expect(root.textContent).not.toContain('Looks good');
});

it('leads with the answer whenever there is one to give, however few', async () => {
	/* The lead is chosen by state, not by the bigger pile. */
	const fewWaiting = await render(card({ matched: 344, waiting: 13 }));
	expect(lead(fewWaiting)).toBe('Yes (13)');

	unmount(mounted as Record<string, unknown>);
	mounted = null;
	host.remove();

	const manyWaiting = await render(card({ matched: 344, waiting: 2444 }));
	expect(lead(manyWaiting)).toBe('Yes (2,444)');
});

it('answers a question with a yes rather than with a count and a neutral verb', async () => {
	/* The lead is a yes; the count line says how many. */
	const root = await render(card({ matched: 344, waiting: 13 }));

	expect(lead(root)).toBe('Yes (13)');
	expect(root.textContent).not.toContain('Answer 13');
});

it('wears ONE shape on the lead whichever of the two acts it is about', async () => {
	/* One label shape, Yes (N), whichever act leads. */
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
	/* The refusal writes a record History can take back. */
	const root = await render(card({ matched: 344, waiting: 13 }));

	openMenu(root);
	menuRow('No (13)').click();
	flushSync();
	for (let turn = 0; turn < 4; turn += 1) await tick();

	expect(rejectLookAlikes).toHaveBeenCalledWith('person-1');
});

it('refuses the matches where the matches are what the yes confirms', async () => {
	/* The menu row follows the lead. */
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
	/* The act that did not lead lives on the person's own screen. */
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
	/* Nothing to do: the card is the numbers and the name. */
	const root = await render(card({ matched: 0, waiting: 0 }));

	expect(root.querySelector('.row')).toBeNull();
	expect(root.textContent).toContain('Nothing needs your input');
});

it('agrees with every match for the person rather than with the faces on the card', async () => {
	/* Asked of the person, not the card's crops; nothing waiting, so the nod leads. */

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
		.querySelector('.wall > li a')
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
	const person = root.querySelector('.wall > li') as HTMLElement;
	const link = person.querySelector(
		'a[href="/organize/known-people/person-1?show=suggested"]'
	) as HTMLAnchorElement;
	const opened = vi.fn((event: Event) => event.preventDefault());
	link.addEventListener('click', opened);

	person
		.querySelector('.counts')
		?.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));

	expect(opened).toHaveBeenCalledTimes(1);
	expect(person.querySelector('.card')?.classList.contains('opens')).toBe(true);
});

it('asks the PERSON from the menu as well as from the lead half', async () => {
	/* Every press is asked of the person, never the card's own list. */
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
	/* The crops' address, naming the tab with something on it. */
	const root = await render(card());

	openMenu(root);
	menuRow('Show me').click();
	flushSync();
	await tick();

	expect(goto).toHaveBeenCalledWith('/organize/known-people/person-1?show=suggested');
});

it('opens the attributed faces from a card whose act is about them', async () => {
	/* Nothing waiting, six attributed: the attributed tab. */
	const root = await render(card({ matched: 6, waiting: 0 }));

	const crops = root.querySelector('a[href^="/organize/known-people/"]');
	expect(crops?.getAttribute('href')).toBe('/organize/known-people/person-1?show=matched');
});

it('narrows itself not at all by how a face was named', async () => {
	/* No tabs on this wall; the one narrowing is the starters control (its own test). */
	const root = await render(card());

	expect(root.querySelector('.narrowing')).toBe(null);
	// The read asks for everybody.
	expect(identifiedPeople).toHaveBeenCalledWith(expect.objectContaining({ limit: 24 }), null, '');
});

/* The control fits the narrowest card: the card's width and the lead's short words are pinned;
 * the box itself is measured in a real engine. */
describe('the one control fits the narrowest card this wall draws', () => {
	const source = readFileSync('src/lib/components/organize/IdentifiedPanel.svelte', 'utf8');

	it('draws its cards on the one Organize wall, in the one Organize card', () => {
		// The wall FaceGroups uses too.
		expect(source).toContain('<CardWall cards={paging.cards}>');
		expect(source).toContain('<DecisionCard opens=');
		expect(source).not.toContain('grid-template-columns');
	});

	it('keeps the refused words off the card', () => {
		// The removed wordings, as literals.
		expect(source).not.toContain("'These matches are right'");
		expect(source).not.toContain('`These matches are right');
		expect(source).not.toContain("'Matches are right'");
		expect(source).not.toContain("'Confirm all'");
	});
});

describe('a person the vault conceals', () => {
	it('says Hidden on the card rather than Not named', async () => {
		/* A card with no id is the concealed gather, worded as the vault's "Hidden". */
		const root = await render(card({ person_id: null, person_name: null }));

		expect(root.querySelector('.who')?.textContent?.trim()).toBe('Hidden');
		expect(root.textContent).not.toContain('Not named');
	});
});
