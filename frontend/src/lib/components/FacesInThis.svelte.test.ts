/* The face strip's two destructive verbs, and the one question they share.
 *
 * Ignoring a face and removing it are one question with one answer behind it, so what is worth a
 * test is the JOIN: that the box ticked on either of them is written once, and that once it is
 * written NEITHER asks again. A test of one verb would pass with the pair wired to two keys.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { oneFaceSays } from '$lib/components/faces/verbs';

vi.mock('$lib/shell/session.svelte', () => ({ session: { isAdmin: true } }));
vi.mock('$app/navigation', () => ({ goto: vi.fn(), beforeNavigate: vi.fn() }));
vi.mock('$app/paths', () => ({ resolve: (path: string) => path }));

/** One face on one file. Unnamed and in no pile, so the card draws the shortest run of verbs. */
const A_FACE = {
	track_id: 'track-1',
	asset_id: 'asset-1',
	person_id: null,
	person_name: null,
	started_ms: 0,
	ended_ms: 2_000,
	/* Its picture was taken well after it was first seen, which is what makes the two answers to
	   "where does pressing it play from" different numbers. */
	picture_ms: 1_500,
	art: null,
	pile_id: null,
	pile_status: null
};

const setAsideFaces = vi.fn(async () => ({ changed: 1, skipped: 0, offered: 0 }));
const removeFaces = vi.fn(async () => ({ changed: 1, skipped: 0, offered: 0 }));

vi.mock('$lib/people/faces.svelte', () => ({
	facesOf: vi.fn(async () => [A_FACE]),
	formatRange: () => '0:00 to 0:02',
	cropUrl: () => '/api/faces/track-1/crop',
	nameFaces: vi.fn(async () => ({ changed: 1, skipped: 0, offered: 0 })),
	rejectFace: vi.fn(async () => ({})),
	removeFaces: (...args: unknown[]) => removeFaces(...(args as [])),
	setAsideFaces: (...args: unknown[]) => setAsideFaces(...(args as []))
}));

vi.mock('$lib/people/people.svelte', () => ({
	peopleNamed: vi.fn(async () => []),
	people: { items: [], load: vi.fn(async () => {}), create: vi.fn(async () => ({})) }
}));

/** The account's own answers, which is where the shared guard lives. */
const stored = vi.hoisted(() => ({ state: {} as Record<string, string> }));
const put = vi.fn(async () => ({}));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async () => ({ state: stored.state })),
		put: (...args: unknown[]) => put(...(args as [])),
		post: vi.fn(async () => ({}))
	},
	request: vi.fn(async () => ({})),
	ApiError: class extends Error {}
}));

import FacesInThis from './FacesInThis.svelte';
import source from './FacesInThis.svelte?raw';
import { facesOf } from '$lib/people/faces.svelte';
import { goto } from '$app/navigation';
import { pileHref } from '$lib/organize/addresses';
import { rereadInterfaceState } from '$lib/shell/interface-state.svelte';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	stored.state = {};
	rereadInterfaceState();
});

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
	// The dialogs are portalled to the end of the document, so taking the host away does not take
	// them with it, and one left standing is found by the next test in this file.
	for (const stale of document.querySelectorAll('.sheet, .veil')) stale.remove();
});

/** The strip, after the faces and the account's answers have both landed. */
async function strip(extra: Record<string, unknown> = {}): Promise<HTMLElement> {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(FacesInThis, { target: host, props: { id: 'asset-1', ...extra } });
	for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
	flushSync();
	return host;
}

/** A verb on the card, by the label it carries for a screen reader. */
function verb(label: string): HTMLElement | null {
	const found = host.querySelector(`.doings [aria-label="${label}"]`);
	return (found as HTMLElement | null)?.closest('button') ?? null;
}

/** The question on screen, wherever it has been portalled to. `.sheet` is `Modal`'s own box; the
 *  role is `alertdialog` on these, because a confirm is the insistent flavour. */
function asking(): HTMLElement | null {
	return document.querySelector('.sheet');
}

/** One control inside the question: its two buttons, or the row that offers to stop asking. */
function inAsking(what: string): HTMLElement | null {
	return document.querySelector(`.sheet ${what}`);
}

function press(element: HTMLElement | null): void {
	element?.click();
	flushSync();
}

/** After a verb has been pressed: the act is a promise, and the card's buttons are disabled while
 *  it is in flight, so a second press in the same turn lands on nothing. */
async function settle(): Promise<void> {
	for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
	flushSync();
}

/** The card's press: the one control the picture and the ground around the name belong to. */
function cardPress(): HTMLElement | null {
	return host.querySelector('.card .whole');
}

/** One rule of the component's own stylesheet, by its whole selector at the start of a line. */
function ruleFor(selector: string): string {
	const style = /<style[^>]*>([\s\S]*?)<\/style>/.exec(source)?.[1] ?? '';
	const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	return new RegExp(`\\n\\s*${escaped} \\{([^}]*)\\}`).exec(style)?.[1] ?? '';
}

describe('what the card is', () => {
	/*
	 * Clicking the card's empty space acts as clicking the thumbnail: a card is one object, so the
	 * ground beside the name must not be a dead patch in something that opens.
	 *
	 * Not asked as containment: a person's name links to their page and an unnamed face's opens its
	 * group, and a link cannot sit inside the press (an anchor in a button is markup browsers take
	 * apart). So the press spans the whole card and the name's column is laid OVER it, taking no
	 * pointer except on the name: the ground is still the press, which is the property. What is
	 * asked is that arrangement, in the stylesheet that makes it, since a document that lays
	 * nothing out cannot say which element a pointer lands on.
	 */
	it('is ONE press over the whole card, with the name laid over it and the ground still the press', async () => {
		await strip({ seekable: true, onseek: () => {} });

		const press = cardPress();
		expect(press?.tagName).toBe('BUTTON');
		expect(press?.querySelector('img')).not.toBeNull();
		expect(press?.getAttribute('aria-label')).toBe('Play from 0:00 to 0:02');
		// The name is the card's, beside the press, not inside it.
		expect(host.querySelector('.card > .beside .who')).not.toBeNull();
		expect(press?.querySelector('.who')).toBeNull();

		expect(ruleFor('.card :global(.whole)')).toMatch(/grid-column: 1 \/ -1;/);
		expect(ruleFor('.beside')).toMatch(/grid-column: 2;/);
		expect(ruleFor('.beside')).toMatch(/pointer-events: none;/);
		expect(ruleFor('.goes')).toMatch(/pointer-events: auto;/);
	});

	it('plays from the moment of its picture, not the first frame the face was seen in', async () => {
		/* The card shows the clearest view of the face; pressing it means "play this". Not from
		   `started_ms`: where the face first appeared, often a blur turning towards the camera. */
		const onseek = vi.fn();
		await strip({ seekable: true, onseek });

		cardPress()?.click();

		expect(onseek).toHaveBeenCalledWith({ ms: 1_500 });
	});

	it('leaves the verbs outside that press, where a press on one is not a press on the card', async () => {
		/* A button inside a button is markup browsers disagree about, and the card's own press would
		   swallow every click meant for a verb. Siblings raised over the card instead (the media
		   tile's arrangement) so there is nothing to stop propagating. */
		await strip({ seekable: true, onseek: () => {} });

		const ignore = verb('Discard');

		expect(ignore).not.toBeNull();
		expect(ignore?.closest('.whole')).toBeNull();
		expect(host.querySelector('.card .doings')).not.toBeNull();
	});

	it('answers a right-click with nothing of its own, because every verb is already on it', async () => {
		/*
		 * The card has no `ContextMenu`: every row it would offer is already a button on it, and a
		 * right-click menu earns its place only on a surface with more verbs than it can show, or a
		 * selection to address.
		 *
		 * Both halves are asked, because either alone would pass for the wrong reason: the trigger
		 * is what a menu leaves in the markup, and the gesture is let through unprevented so the
		 * browser's own menu answers a picture.
		 */
		await strip({ seekable: true, onseek: () => {} });

		expect(host.querySelector('[data-context-menu-trigger]')).toBeNull();

		const card = host.querySelector('.card') as HTMLElement;
		const wentThrough = card.dispatchEvent(
			new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
		);
		await settle();

		expect(wentThrough).toBe(true);
		expect(document.querySelector('.ui-menu')).toBeNull();
		// And nothing was lost with it: the verbs are on the card, which is the whole argument.
		expect(verb('Discard')).not.toBeNull();
		expect(verb('Delete')).not.toBeNull();
	});

	it('draws no press at all on a file nothing can be played to', async () => {
		/* A photograph. There is no moment to go to, so the card is a card: a ground that answers the
		   pointer and then does nothing is worse than one that never offered. */
		await strip();

		expect(host.querySelector('.card .whole')?.tagName).toBe('DIV');
	});

	it('reserves the room the verbs need from the verbs themselves', async () => {
		/* They are raised out of the flow, so they size nothing: a card left to the name alone would
		   be "Not named yet" wide and one line tall, and the verbs would hang off the end of it. What
		   the stylesheet reserves it from is the count that drew them: a number written in the
		   stylesheet instead would be wrong the first time a face carried three. */
		await strip({ seekable: true, onseek: () => {} });

		const card = host.querySelector('.card') as HTMLElement;
		const drawn = host.querySelectorAll('.doings button').length;

		expect(drawn).toBeGreaterThan(0);
		expect(card.style.getPropertyValue('--verbs')).toBe(String(drawn));
		expect(card.classList.contains('acting')).toBe(true);
	});
});

/*
 * A name on a card goes somewhere: a recognised person's name to their
 * page, by the same address the People chips on the file use, and an unnamed face's words to the
 * group it waits in, the same place "Show the face group" goes. The card's own press is untouched:
 * on a video it still plays from the face's moment.
 */
describe('the name on a card', () => {
	it("links a recognised person's name to their page", async () => {
		vi.mocked(facesOf).mockResolvedValueOnce([
			{ ...A_FACE, person_id: 'person-1', person_name: 'Wren Halloway' }
		] as never);
		const onseek = vi.fn();
		await strip({ seekable: true, onseek });

		const name = host.querySelector('.who') as HTMLElement;
		expect(name.tagName).toBe('A');
		expect(name.getAttribute('href')).toBe('/people/person-1');
		expect(name.textContent).toBe('Wren Halloway');
		expect(name.closest('button')).toBeNull();

		// And the card still plays: the link took the words, not the press.
		cardPress()?.click();
		expect(onseek).toHaveBeenCalledWith({ ms: 1_500 });
	});

	it("opens an unnamed face's group from its words, where Show the face group goes", async () => {
		const grouped = { ...A_FACE, pile_id: 'pile-9', pile_status: 'open' };
		vi.mocked(facesOf).mockResolvedValueOnce([grouped] as never);
		await strip({ seekable: true, onseek: () => {} });

		const name = host.querySelector('.who') as HTMLElement;
		expect(name.tagName).toBe('A');
		expect(name.textContent).toBe('Not named yet');
		expect(name.getAttribute('href')).toBe(pileHref({ id: 'pile-9', status: 'open' }));

		// The same address the verb navigates to, asked of the verb rather than restated here.
		press(verb('Show the face group'));
		await settle();
		expect(vi.mocked(goto)).toHaveBeenCalledWith(name.getAttribute('href'));
	});

	it('leaves the words of a face with no name and no group as words', async () => {
		await strip({ seekable: true, onseek: () => {} });

		const name = host.querySelector('.who') as HTMLElement;
		expect(name.tagName).toBe('SPAN');
		expect(name.textContent).toBe('Not named yet');
		expect(host.querySelector('.beside a')).toBeNull();
	});

	it('says why a face turned away from the camera waits: Sift only asks about it', async () => {
		/* Kept although turned past the quality bar's angle, it is asked about and never named by
		   Sift alone; without the line it reads as a face Sift saw and then forgot. */
		vi.mocked(facesOf).mockResolvedValueOnce([{ ...A_FACE, turned: true }] as never);
		await strip({ seekable: true, onseek: () => {} });

		// A mark on the name's own line with the words for its name and tooltip, never a line of
		// its own: a line under one name would push that card's presses below every other card's.
		const mark = host.querySelector('.naming .turned [aria-label]');
		expect(mark?.getAttribute('aria-label')).toBe('Turned away from the camera');
		expect(host.querySelector('.beside > .turned')).toBeNull();
	});

	it('says nothing of the kind about a face that is not turned', async () => {
		await strip({ seekable: true, onseek: () => {} });

		expect(host.querySelector('.turned')).toBeNull();
	});

	it('draws no door to a group that is gone', async () => {
		/* A pile id with no status is a group grouping has since rebuilt away: no link, no verb. */
		vi.mocked(facesOf).mockResolvedValueOnce([
			{ ...A_FACE, pile_id: 'pile-9', pile_status: null }
		] as never);
		await strip();

		expect(host.querySelector('.beside a')).toBeNull();
		expect(verb('Show the face group')?.hasAttribute('disabled')).toBe(true);
		expect(cardPress()?.tagName).toBe('DIV');
	});
});

/*
 * Every card on the strip is one shape: the same presses in the same places, a press a face cannot
 * take drawn disabled with its reason, never missing, and every card as tall as the tallest so the
 * presses stand on one line across the strip.
 */
describe('the cards on the strip', () => {
	const PRESSES = [
		'Show the face group',
		'Add as person',
		'Remove the name from this',
		'Discard',
		'Delete'
	];

	it('carry the same presses whatever each face can take, the ones it cannot disabled', async () => {
		vi.mocked(facesOf).mockResolvedValueOnce([
			{ ...A_FACE, track_id: 't-grouped', pile_id: 'pile-9', pile_status: 'open' },
			{ ...A_FACE, track_id: 't-alone', turned: true },
			{ ...A_FACE, track_id: 't-named', person_id: 'person-1', person_name: 'Wren Halloway' }
		] as never);
		await strip({ seekable: true, onseek: () => {} });

		const cards = [...host.querySelectorAll('.card')] as HTMLElement[];
		const shapes = cards.map((card) =>
			[...card.querySelectorAll('.doings button')].map((one) =>
				one.getAttribute('aria-label')?.replace('Wren Halloway', 'the name')
			)
		);
		expect(shapes).toEqual([PRESSES, PRESSES, PRESSES]);
		expect(cards.map((card) => card.style.getPropertyValue('--verbs'))).toEqual(['5', '5', '5']);

		const off = (card: HTMLElement) =>
			[...card.querySelectorAll('.doings button:disabled')].map((one) =>
				one.getAttribute('aria-label')
			);
		expect(off(cards[0])).toEqual(['Remove the name from this']);
		expect(off(cards[1])).toEqual(['Show the face group', 'Remove the name from this']);
		expect(off(cards[2])).toEqual(['Show the face group']);
	});

	it('says why a press cannot act, in its tooltip', async () => {
		await strip();
		const disabled = verb('Show the face group') as HTMLElement;
		disabled
			.closest('.target')
			?.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, pointerType: 'mouse' }));
		await new Promise((done) => setTimeout(done, 400));
		flushSync();
		expect(document.querySelector('[role="tooltip"]')?.textContent?.trim()).toBe('Not in a group');
	});

	it('stretches every card to the tallest, so the presses stand on one line', () => {
		expect(ruleFor('li')).toContain('display: flex');
		expect(ruleFor('li > .card')).toContain('flex: 1');
	});
});

/*
 * Whether Sift is asking anything about this name.
 *
 * The mark says which, in the three phrases every faces screen uses, because what a beginner needs
 * is whether there is anything here to do. The settled state wears nothing, the part most worth
 * pinning: it is most of the cards, and a mark on every one would bury the two that mean something.
 */
describe('the mark beside the name', () => {
	/** One face on this file, named, in one of the three states. */
	async function named(attribution: string | null): Promise<HTMLElement> {
		vi.mocked(facesOf).mockResolvedValueOnce([
			{
				...A_FACE,
				person_id: 'person-1',
				person_name: 'Wren Halloway',
				attribution
			}
		] as never);
		return strip();
	}

	/*
	 * No chip beside the name: this strip says who is in the picture, and where each name came from
	 * is the review queue's question, answered in full on the screens built for it.
	 */
	it.each(['matched', 'confirmed'])('names a %s face and nothing else', async (how) => {
		const root = await named(how as 'matched' | 'confirmed');

		expect(root.querySelector('.naming .who')?.textContent?.trim()).toBe('Wren Halloway');
		expect(root.querySelector('.naming .face-mark')).toBeNull();
		expect(root.textContent).not.toContain('Named by Sift');
		expect(root.textContent).not.toContain('Suggested by Sift');
	});

	/*
	 * Except a QUESTION: without a mark a name Sift is asking about would read here as settled, the
	 * one screen where a question looked like a name. It wears the mark the faces screens use,
	 * named as they name it.
	 */
	it('marks a name Sift is asking about, in the words the tabs use', async () => {
		const root = await named('suggested');

		expect(root.querySelector('.naming .who')?.textContent?.trim()).toBe('Wren Halloway');
		const mark = root.querySelector('.naming .face-mark [role="img"]');
		expect(mark?.getAttribute('aria-label')).toBe('Needs your input');
	});
});

describe('discarding or deleting a face', () => {
	it('asks before ignoring one', async () => {
		await strip();

		press(verb('Discard'));

		expect(asking()?.textContent).toContain('Discard this face?');
		expect(setAsideFaces).not.toHaveBeenCalled();
	});

	it('asks before deleting one', async () => {
		await strip();

		press(verb('Delete'));

		expect(asking()?.textContent).toContain('Delete this face?');
		expect(removeFaces).not.toHaveBeenCalled();
	});

	it('draws the destructive verb with no box of its own', async () => {
		// Delete has no outline: three quiet glyphs and one outlined one reads as the outlined one
		// being the important one.
		await strip();

		expect(verb('Delete')?.className).toContain('danger-ghost');
		expect(verb('Delete')?.className).not.toContain('danger-quiet');
	});

	/*
	 * The two glyphs side by side each say what they will do, in one sentence, and the question
	 * under each press states the same words from one source, so tooltip and dialog cannot come
	 * apart. Hovered the way a person hovers (a pointer move; see `Tooltip`), and the words read
	 * off the bubble actually drawn.
	 */
	it.each([
		['Discard', 'Move this face to Discarded. It stays listed there and can be restored.'],
		['Delete', 'Delete this face from the file permanently. The file itself is untouched.']
	])('says on %s what it will do, and asks in the same words', async (label, sentence) => {
		await strip();

		verb(label)
			?.closest('.wrap')
			?.dispatchEvent(new PointerEvent('pointermove', { bubbles: true }));
		await vi.waitFor(() =>
			expect(document.querySelector('[role="tooltip"]')?.textContent?.trim()).toBe(sentence)
		);
		// The accessible name is still the verb; the sentence is its description.
		expect(verb(label)?.getAttribute('aria-label')).toBe(label);

		press(verb(label));
		expect(asking()?.textContent).toContain(sentence);
	});

	it('keeps the plain name on a verb that has no sentence of its own', () => {
		// Every other verb on the card is its own explanation; only the pair that both take the face
		// away needed to say which one can be taken back.
		expect(oneFaceSays('name')).toBeUndefined();
		expect(oneFaceSays('show-group')).toBeUndefined();
	});
});

describe("the card's verbs", () => {
	it('draws every glyph at the size the button gives it, and Delete last and apart', async () => {
		await strip();
		const buttons = [...host.querySelectorAll('.doings button')];
		expect(buttons.length).toBeGreaterThan(1);
		// The button's own glyph size, never a smaller one written per card.
		expect(host.querySelectorAll('.doings .icon.size-16')).toHaveLength(0);
		expect(host.querySelectorAll('.doings .icon.size-18').length).toBe(buttons.length);
		// The one that destroys something is the last, inside the wider step.
		expect(buttons.at(-1)?.getAttribute('aria-label')).toBe('Delete');
		expect(verb('Delete')?.closest('.apart')).not.toBeNull();
		expect(verb('Discard')?.closest('.apart')).toBeNull();
	});
});

describe('the offer to stop being asked', () => {
	it('writes nothing when the question is answered without ticking it', async () => {
		await strip();
		press(verb('Discard'));

		press(inAsking('button.confirm'));
		await settle();

		expect(setAsideFaces).toHaveBeenCalledTimes(1);
		expect(put).not.toHaveBeenCalled();
	});

	it('writes nothing when the box is ticked and the question is then cancelled', async () => {
		// A tick on a dialog somebody backed out of is not an agreement to anything.
		await strip();
		press(verb('Delete'));

		press(inAsking('.tick'));
		press(inAsking('button.cancel'));

		expect(removeFaces).not.toHaveBeenCalled();
		expect(put).not.toHaveBeenCalled();
	});

	it('is ONE answer: ticked on Discard, and Delete stops asking too', async () => {
		/* The whole point of the shared key. Wired to two keys this passes for the verb it was
		   ticked on and fails here, which is why both verbs are exercised in one test. */
		await strip();
		press(verb('Discard'));
		press(inAsking('.tick'));
		press(inAsking('button.confirm'));
		await settle();

		expect(put).toHaveBeenCalledTimes(1);

		press(verb('Delete'));

		expect(asking()).toBeNull();
		expect(removeFaces).toHaveBeenCalledTimes(1);
	});

	it('skips both questions for an account that answered on another machine', async () => {
		// It follows the ACCOUNT, so the answer arrives with the one read rather than from this
		// browser's storage.
		stored.state = { 'confirm.face_removal': 'skip' };
		await strip();

		press(verb('Discard'));
		await settle();
		press(verb('Delete'));

		expect(asking()).toBeNull();
		expect(setAsideFaces).toHaveBeenCalledTimes(1);
		expect(removeFaces).toHaveBeenCalledTimes(1);
	});
});

describe('a record read again for the same file', () => {
	it('keeps the faces drawn, and asks again only when the file is another one', async () => {
		/* The caller hands `id` through an object that is replaced whenever the record is read
		   again, which is what a playing clip's view being counted does. */
		const shown = $state({ record: { id: 'asset-1', views: 0 } });
		host = document.createElement('div');
		document.body.append(host);
		mounted = mount(FacesInThis, {
			target: host,
			props: {
				get id() {
					return shown.record.id;
				}
			}
		});
		await settle();
		expect(facesOf).toHaveBeenCalledTimes(1);
		expect(host.querySelectorAll('.card').length).toBe(1);

		const seen: number[] = [];
		const watch = new MutationObserver(() => seen.push(host.querySelectorAll('.card').length));
		watch.observe(host, { childList: true, subtree: true });
		shown.record = { id: 'asset-1', views: 1 };
		flushSync();
		await settle();
		watch.disconnect();

		expect(facesOf).toHaveBeenCalledTimes(1);
		expect(seen).not.toContain(0);
		expect(host.querySelectorAll('.card').length).toBe(1);

		shown.record = { id: 'asset-2', views: 0 };
		flushSync();
		await settle();
		expect(facesOf).toHaveBeenCalledTimes(2);
		expect(vi.mocked(facesOf).mock.calls[1][0]).toBe('asset-2');
	});
});
