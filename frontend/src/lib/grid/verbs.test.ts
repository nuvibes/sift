/*
 * The bar and the right-click menu cannot come to offer different things.
 *
 * Two halves, because there are two ways for that to stop being true and a test for only one of
 * them would go on passing through the other.
 *
 * 1. The DECLARATION: every verb reaches both surfaces, except the ones that say in so many words
 *    that they only make sense pointed at one file. A verb quietly left off the bar fails here.
 * 2. The MARKUP: both surfaces render the declaration through the two renderers and hand-write no
 *    rows of their own. This is the half that catches somebody adding a button straight into the
 *    bar, which is exactly how two such lists drift apart, and which the first
 *    half cannot see, because a hand-written button is not in the declaration to be counted.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { barShape, menuGroups, VERB_GROUPS, type VerbPick } from '$lib/components/common/verbs';
import { entityVerbs } from '$lib/components/entity/verbs';
import { EVERY_BOX } from '$lib/entity/enrichment.svelte';
import { phoneWidth } from '$lib/components/common/phone-width.svelte';
import {
	barVerbs,
	fileVerbs,
	flatVerbs,
	menuVerbs,
	NO_WRITABLE_FOLDER,
	runNowVerb,
	type FileVerbHandlers,
	type Verb
} from './verbs';

const NOTHING = () => {};

/** A list that answers nothing: the five places a file can be put are lists, not presses. */
const PICK: VerbPick = {
	kind: 'tag',
	plural: 'tags',
	ask: async () => ({ choices: [], more: 0 }),
	pick: async () => 'landed'
};

const HANDLERS: FileVerbHandlers = {
	tag: PICK,
	rename: NOTHING,
	collect: PICK,
	assign: PICK,
	site: PICK,
	photoSet: PICK,
	song: PICK,
	favorite: NOTHING,
	pin: NOTHING,
	rate: NOTHING,
	move: NOTHING,
	share: NOTHING,
	visibility: NOTHING,
	hide: NOTHING,
	save: NOTHING,
	link: NOTHING,
	autoEnrich: NOTHING,
	enrich: NOTHING,
	compress: NOTHING,
	edit: NOTHING,
	gif: NOTHING,
	remove: NOTHING
};

const SAVING = {
	label: (_type: string, count: number) => (count === 1 ? 'Save' : 'Save all'),
	icon: () => 'download' as const
};

function verbsFor(overrides: Partial<Parameters<typeof fileVerbs>[0]> = {}): Verb[] {
	return fileVerbs(
		{
			isAdmin: true,
			canSave: true,
			showingHidden: false,
			count: 3,
			canMove: true,
			canCompress: true,
			handlers: HANDLERS,
			...overrides
		},
		SAVING
	);
}

describe('the declared verbs reach both surfaces', () => {
	it('offers everything the definition of done names', () => {
		// Named one by one rather than counted. A count passes on the wrong twelve.
		expect(verbsFor().map((verb) => verb.id)).toEqual([
			'add',
			'pin',
			'rate',
			'link',
			'save',
			'move',
			'rename',
			'compress',
			'auto-enrich',
			'enrich',
			'share',
			'visibility',
			'hide',
			'delete'
		]);
	});

	it('gives the bar every verb except the ones that need one file', () => {
		const all = verbsFor();
		const bar = barVerbs(all).map((verb) => verb.id);
		// Through the grouping, on both sides. A parent row is a place to look rather than a thing
		// to do, so the question "does every verb reach both surfaces" is about what is under it,
		// and comparing the top level instead is how a verb could be hidden from the bar by tucking
		// it into a group.
		const menu = flatVerbs(menuVerbs(all)).map((verb) => verb.id);

		// The menu is the whole list (in its groups' order), so any verb absent from the bar has to
		// have said why.
		expect([...menu].sort()).toEqual(
			flatVerbs(all)
				.map((verb) => verb.id)
				.sort()
		);
		const missing = menu.filter((id) => !bar.includes(id));
		const excused = flatVerbs(all)
			.filter((verb) => verb.singleOnly)
			.map((verb) => verb.id);
		expect(missing).toEqual(excused);

		// And the excuse itself is named, not merely present. Without this, keeping a verb off the
		// bar is one word away: mark it `singleOnly` and the assertion above agrees with itself.
		// Two verbs only make sense pointed at one file. Copying forty links is not a thing; and
		// forty files have forty different answers to "who can see this and how", so a report over
		// a selection would have to pick one of them or collapse them into a sentence that is not
		// true of its members.
		expect(missing).toEqual(['link', 'visibility']);
	});

	it('puts every place a file can go under one row, tagging included', () => {
		// Tagging is inside this group. That a tag is a word put ON a file rather than a place to
		// file it under is true of the data and not visible from the menu: all six rows open out
		// into a list of named things with a tick beside each, so one standing outside would read
		// as the odd one out rather than as the important one.
		const group = verbsFor().find((verb) => verb.id === 'add');
		expect(group?.label).toBe('Add to');
		expect(group?.children?.map((child) => child.id)).toEqual([
			'assign',
			'site',
			'collect',
			'photo_set',
			'tag',
			'song',
			'favorite'
		]);
		expect(group?.run, 'a row that both opens out and acts would act on the way past').toBe(
			undefined
		);
		// The five places are their lists, declared, and nothing else: a press beside the list would
		// let the selection bar open a sheet of its own where every menu opens the list.
		for (const place of (group?.children ?? []).filter((child) => child.id !== 'favorite')) {
			expect(place.pick, place.id).toBe(PICK);
			expect(place.run, place.id).toBeUndefined();
		}
		// And nothing is left at the top level pretending to be it.
		expect(verbsFor().some((verb) => verb.id === 'tag')).toBe(false);
	});

	it('words a grouped verb on the bar as the path a menu would have made you walk', () => {
		// The bar has no path to read, so it reads the two halves joined. Each half is written once
		// ("Add to" on the group, "Person" on the row), which is what stops the words the same
		// verb wears on the two surfaces from drifting apart.
		const bar = barVerbs(verbsFor());
		expect(bar.find((verb) => verb.id === 'assign')?.label).toBe('Add to Person');
		expect(bar.find((verb) => verb.id === 'collect')?.label).toBe('Add to Collection');
		// And the group itself is not on the bar: it is a place to look, not a thing to do.
		expect(bar.map((verb) => verb.id)).not.toContain('add');
	});

	it('still keeps a one-file verb off the bar when it is inside a group', () => {
		// The excuse has to survive the grouping. Reading `singleOnly` off the parent instead would
		// put a verb that means nothing pointed at forty files onto the surface that only ever
		// addresses forty files.
		const grouped: Verb[] = [
			{
				id: 'add',
				label: 'Add to',
				icon: 'add',
				children: [
					{ id: 'assign', label: 'Person', icon: 'person', run: NOTHING },
					{ id: 'link', label: 'Link', icon: 'link', singleOnly: true, run: NOTHING }
				]
			}
		];
		expect(barVerbs(grouped).map((verb) => verb.id)).toEqual(['assign']);
	});

	it('greys compress with its reason where Sift may not write, rather than leaving it out', () => {
		// The copy lands beside the original, which is a write into somebody's library. Most
		// libraries are indexed read-only, and on those the verb is drawn refused with the reason:
		// absent, it read as a missing feature on every file.
		const compress = verbsFor({ canCompress: false }).find((verb) => verb.id === 'compress');
		expect(compress?.disabled).toBe(true);
		expect(compress?.why).toBe(NO_WRITABLE_FOLDER);
		expect(verbsFor().find((verb) => verb.id === 'compress')?.disabled).toBeUndefined();
	});

	it('leaves compress out for a guest', () => {
		// Writing into a library is an admin's, and the server refuses it whatever a browser draws.
		const asGuest = verbsFor({ isAdmin: false, canCompress: true });
		expect(asGuest.map((verb) => verb.id)).not.toContain('compress');
	});

	it('offers compress on the bar as well as the menu', () => {
		// The whole point of the verb: it is bulk-capable, and the bar is where a selection acts.
		const all = verbsFor();
		expect(barVerbs(all).map((verb) => verb.id)).toContain('compress');
	});

	it('puts the rating behind a press, on both surfaces', () => {
		// The bar and the menu render from one declaration, so this is what makes "a button with the
		// answers behind it" true of both rather than of whichever one somebody edited. The walls of
		// entities deliberately do NOT set it: their own test asserts the other half.
		const rate = verbsFor().find((verb) => verb.id === 'rate');
		expect(rate?.stars).toBe(true);
		expect(rate?.flyout).toBe(true);
	});

	it('takes the same verbs away from a guest on both surfaces', () => {
		// The failure this prevents is subtler than a missing button: a verb offered in the menu and
		// not in the bar, to one role only, is the kind of thing nobody notices until a guest
		// right-clicks.
		const asGuest = verbsFor({ isAdmin: false });
		const bar = new Set(barVerbs(asGuest).map((verb) => verb.id));
		for (const verb of flatVerbs(menuVerbs(asGuest))) {
			if (verb.singleOnly) continue;
			expect(bar.has(verb.id), `${verb.id} is in the menu but not the bar`).toBe(true);
		}
		expect(asGuest.map((verb) => verb.id)).not.toContain('delete');
		expect(asGuest.map((verb) => verb.id)).not.toContain('move');
	});

	it('leaves Move out where there is nowhere to move to', () => {
		expect(verbsFor({ canMove: false }).map((verb) => verb.id)).not.toContain('move');
	});

	/** The heart, wherever it is: inside the group for an admin, on its own for a guest. */
	function heartOf(overrides: Partial<Parameters<typeof fileVerbs>[0]> = {}): Verb | undefined {
		return flatVerbs(verbsFor(overrides)).find((verb) => verb.id === 'favorite');
	}

	it('says which way the heart goes only when it is pointed at one file', () => {
		expect(
			heartOf({ subject: { media_type: 'video', favorite: true, concealed: false } })?.label
		).toBe('Remove from favorites');
		// No subject is a set, and a set has no single answer, so it names the wall instead.
		expect(heartOf()?.label).toBe('Favorites');
	});

	it('says the heart goes off when every file in the set is already a favorite', () => {
		// A bar cannot read one file's heart, but it can read all of them. A selection of nothing
		// but favorites must not offer "Favorite": pressing it would favorite what is already
		// favorited, which looks exactly like the button doing nothing.
		expect(heartOf({ allFavorite: true })?.label).toBe('Remove from favorites');
		// And a set that is only partly favorited has no single answer.
		expect(heartOf({ allFavorite: false })?.label).toBe('Favorites');
	});

	it('does not put "Add to" in front of the heart when it reverses', () => {
		/* The one row in that group whose meaning flips. Every other child is an OBJECT and the flat
		   surface joins it to the group's phrase; "Add to Remove from favorites" is not a sentence,
		   so this row says the whole thing itself. */
		expect(heartOf()?.label).toBe('Favorites');
		expect(heartOf({ allFavorite: true })?.label).toBe('Remove from favorites');
		// And the join is still on for the rows it works for, so this is about the heart rather
		// than about the joining having been turned off.
		expect(flatVerbs(verbsFor()).find((verb) => verb.id === 'assign')?.label).toBe('Add to Person');
	});

	it('gives a guest the heart on its own, outside the group', () => {
		// Everything else in that group is shared vocabulary: what it changes is what everybody
		// else's screens return. The heart is not: it changes one account's own screen.
		const asGuest = verbsFor({ isAdmin: false });
		expect(asGuest.map((verb) => verb.id)).toContain('favorite');
		expect(asGuest.map((verb) => verb.id)).not.toContain('add');
		// On its own, with no door before it, its words are the whole instruction.
		expect(asGuest.find((verb) => verb.id === 'favorite')?.label).toBe('Add to favorites');
	});

	it('calls it Trim over a clip and Modify over a photograph', () => {
		// One panel behind both. "Edit" over a clip says nothing about what the panel will offer,
		// and the two are asserted together because a label tested on one kind of file cannot be
		// told apart from a constant.
		//
		// The picture's word is "Modify", not "Edit": it sits one row above Rename in a file's own
		// menu, and half the application means renaming by "Edit", so the row that rewrites the
		// pixels and the row that rewrites the name would read as one offer twice.
		const clip = verbsFor({
			count: 1,
			subject: { media_type: 'video', favorite: false, concealed: false }
		});
		const photograph = verbsFor({
			count: 1,
			subject: { media_type: 'image', favorite: false, concealed: false }
		});

		expect(clip.find((verb) => verb.id === 'edit')?.label).toBe('Trim');
		expect(photograph.find((verb) => verb.id === 'edit')?.label).toBe('Modify');
	});
});

describe('neither surface writes a verb of its own', () => {
	const GRID = readFileSync('src/lib/components/AssetGrid.svelte', 'utf8');

	/** The markup of the bar's own snippet, which is the only place a bar button could be added. */
	function barMarkup(source: string): string {
		const start = source.indexOf('<ActionBar');
		const end = source.indexOf('</ActionBar>', start);
		expect(start, 'the grid has no action bar').toBeGreaterThan(-1);
		expect(end, 'the action bar is not closed').toBeGreaterThan(start);
		return source.slice(start, end);
	}

	/** The markup of the tile menu's `items` snippet. */
	function menuMarkup(source: string): string {
		const start = source.indexOf('{#snippet items()}');
		const end = source.indexOf('{/snippet}', start);
		expect(start, 'the grid has no tile menu').toBeGreaterThan(-1);
		return source.slice(start, end);
	}

	it('renders the bar from the declared list and nothing else', () => {
		const bar = barMarkup(GRID);
		expect(bar).toContain('<VerbButtons');
		// A hand-written button in the bar is the drift. There is deliberately no allowance for one:
		// a bar action that the menu cannot offer belongs in the declaration with a reason on it.
		expect(bar, 'a button was written into the bar instead of declared as a verb').not.toMatch(
			/<button/
		);
	});

	it('renders the menu from the declared list and nothing else', () => {
		const menu = menuMarkup(GRID);
		expect(menu).toContain('<VerbMenuItems');
		expect(menu, 'a row was written into the menu instead of declared as a verb').not.toMatch(
			/<ContextMenuItem/
		);
	});

	it('finds the markup it claims to be checking', () => {
		// An empty haystack passes every "does not contain" assertion. This is the liveness check in
		// front of the two above.
		expect(barMarkup(GRID).length).toBeGreaterThan(40);
		expect(menuMarkup(GRID).length).toBeGreaterThan(40);
	});
});

describe('the two verbs that change a disk ask before they run', () => {
	/* Read from the host rather than from the grid: the wiring lives in the one place every surface
	   showing files goes through, not in a copy any one surface keeps for itself. */
	const HOST = readFileSync('src/lib/components/common/FileVerbs.svelte', 'utf8');

	/*
	 * Move and Delete both write to somebody's disk, and both are wired the same way: the verb opens
	 * a sheet, and only the sheet's own answer calls the action. What this asserts is that wiring:
	 * a verb that called the action directly would move files on the first click, with the count
	 * still on screen and nothing said.
	 */
	it('sends Move to the sheet, never straight to the action', () => {
		expect(HOST).toMatch(/move:\s*\(ids: string\[\]\) => void askToMove\(ids\)/);
		// The action itself is reachable from exactly one place: the sheet's confirm.
		const calls = [...HOST.matchAll(/actions\.move\(/g)];
		expect(calls, 'the move action is called from more than one place').toHaveLength(1);
		const confirm = HOST.slice(HOST.indexOf('<MoveDialog'));
		expect(confirm).toContain('actions.move(');
	});

	it('sends Delete to the sheet, never straight to the action', () => {
		expect(HOST).toMatch(/remove:\s*\(ids: string\[\]\) => askToDelete\(ids\)/);
		const calls = [...HOST.matchAll(/actions\.remove\(/g)];
		expect(calls, 'the delete action is called from more than one place').toHaveLength(1);
	});

	it('names a count in the question', () => {
		const sheet = readFileSync('src/lib/components/MoveDialog.svelte', 'utf8');
		// The count is in the question. "Move these?" with no number is how somebody moves thirty
		// files meaning to move one.
		expect(sheet).toMatch(/Move \$\{counted\(count\)\} files/);
		/* What the button REFUSES is asserted by rendering it, in MoveDialog.svelte.test.ts, and
		   not here. A copy of the `confirmDisabled` line read out of the component is not a
		   check: it is a second copy of the component, and it goes stale the moment the
		   expression changes while the behaviour it describes is still correct. */
	});
});

describe('a menu opened inside a selection', () => {
	const GRID = readFileSync('src/lib/components/AssetGrid.svelte', 'utf8');
	const WALLS = [
		'src/routes/people/+page.svelte',
		'src/routes/sites/+page.svelte',
		'src/routes/collections/+page.svelte',
		'src/routes/tags/+page.svelte'
	];

	/*
	 * Right-clicking a row that is part of a selection acts on the whole selection, except for the
	 * verbs that only mean something pointed at one thing. Copy link has to copy the link of the row
	 * under the pointer, not of whichever of forty happens to be first.
	 *
	 * The renderer decides that, and it can only decide it if the surface tells it which row the
	 * menu was opened on. A surface that stops passing it goes back to opening the wrong file, and
	 * nothing about the menu would look wrong.
	 */
	it('tells the renderer which row it was opened on', () => {
		expect(GRID).toMatch(/<VerbMenuItems[\s\S]{0,200}?subjectId=/);
		for (const wall of WALLS) {
			const source = readFileSync(wall, 'utf8');
			expect(source, `${wall} does not say which row its menu was opened on`).toMatch(
				/<VerbMenuItems[\s\S]{0,200}?subjectId=/
			);
		}
	});
});

describe('which way the hide verb points', () => {
	function hideVerb(overrides: Partial<Parameters<typeof fileVerbs>[0]> = {}) {
		return verbsFor(overrides).find((verb) => verb.id === 'hide');
	}

	it('offers to hide on an ordinary wall', () => {
		expect(hideVerb()?.label).toBe('Hide');
		expect(hideVerb()?.icon).toBe('visibility_off');
	});

	it('offers to unhide on the screen that shows hidden files', () => {
		expect(hideVerb({ showingHidden: true })?.label).toBe('Unhide');
		expect(hideVerb({ showingHidden: true })?.icon).toBe('visibility');
	});

	it('offers to unhide when everything picked is already hidden, wherever it is', () => {
		/* The case the screen cannot answer. With the vault open, hidden files sit on the
		   ordinary walls beside everything else, so a selection of them on Browse must not be
		   offered "Hide" by reading the direction of the action off the same flag as the word:
		   pressing it would hide what is already hidden and report success. */
		expect(hideVerb({ allHidden: true })?.label).toBe('Unhide');
		expect(hideVerb({ allHidden: true })?.icon).toBe('visibility');
	});

	it('offers the PIN, never Hide, on a locked tile', () => {
		/* A locked tile is hidden with the vault shut, so its menu must not say "Hide". Hiding
		   it is false and unhiding it is refused until the PIN opens the vault, so the row asks
		   for the PIN, and a surface with no way to ask offers nothing. */
		let asked = 0;
		const locked = verbsFor({
			allLocked: true,
			count: 1,
			handlers: { ...HANDLERS, unlock: () => (asked += 1) }
		});
		expect(locked.find((verb) => verb.id === 'hide')).toBeUndefined();
		const unlock = locked.find((verb) => verb.id === 'unlock');
		expect([unlock?.label, unlock?.icon]).toEqual(['Unlock', 'lock']);
		unlock?.run?.(['f1']);
		expect(asked).toBe(1);

		const mute = verbsFor({ allLocked: true, count: 1 });
		expect(mute.filter((verb) => verb.id === 'hide' || verb.id === 'unlock')).toEqual([]);
	});

	it('offers to hide when only some of what is picked is hidden', () => {
		/* A mixed selection has no single opposite, so it takes the plain meaning: hide the lot.
		   That is the same rule the heart follows over a mixed set. */
		expect(hideVerb({ allHidden: false })?.label).toBe('Hide');
	});
});

describe('one file is edited, a selection is compressed', () => {
	/* The substitution the right-click menu makes, and the two places it deliberately does not.
	 *
	 * A rectangle or a moment is something you choose for a particular photograph or clip, and
	 * means nothing applied to the next file along, so a menu opened on one file offers Edit.
	 * Anything addressing a SET keeps Compress, which is the same instruction to four hundred files
	 * and is genuinely useful that way.
	 */
	const A_PHOTOGRAPH = { media_type: 'image', favorite: false, concealed: false };
	const AN_ANIMATION = { media_type: 'gif', favorite: false, concealed: false };

	it('offers Edit on a menu opened over one file', () => {
		const ids = verbsFor({ count: 1, subject: A_PHOTOGRAPH }).map((verb) => verb.id);
		expect(ids).toContain('edit');
		expect(ids).not.toContain('compress');
	});

	it('offers one clip all three: Edit, Create GIF and Compress, as its own screen draws them', () => {
		// One table for the tile's menu and the file's own screen.
		const A_CLIP_HERE = { media_type: 'video', favorite: false, concealed: false };
		const ids = verbsFor({ count: 1, subject: A_CLIP_HERE }).map((verb) => verb.id);
		expect(ids.filter((id) => ['edit', 'gif', 'compress'].includes(id))).toEqual([
			'edit',
			'gif',
			'compress'
		]);
	});

	it('offers a phone Compress on a clip, and neither Trim nor Create GIF', () => {
		/* A strip of frames dragged to the frame is what Trim and Create GIF are, and a phone cannot
		   offer it; the one rule (`clipEditsOffered`) is asked by the tile's menu and the file's own
		   screen alike, since both draw this table. */
		const A_CLIP_HERE = { media_type: 'video', favorite: false, concealed: false };
		phoneWidth.yes = true;
		try {
			const ids = verbsFor({ count: 1, subject: A_CLIP_HERE }).map((verb) => verb.id);
			expect(ids).toContain('compress');
			expect(ids).not.toContain('edit');
			expect(ids).not.toContain('gif');
		} finally {
			phoneWidth.yes = false;
		}
	});

	it('keeps Compress over a selection', () => {
		const ids = verbsFor({ count: 12, subject: A_PHOTOGRAPH }).map((verb) => verb.id);
		expect(ids).toContain('compress');
		expect(ids).not.toContain('edit');
	});

	it('keeps Compress on the bar, which never addresses one file in particular', () => {
		// The bar passes no subject even when one thing is picked, because what it acts on is the
		// selection. It must not lose the verb that works on one.
		const ids = verbsFor({ count: 1, subject: null }).map((verb) => verb.id);
		expect(ids).toContain('compress');
		expect(ids).not.toContain('edit');
	});

	it('keeps Compress over a single GIF', () => {
		// The one kind the editor refuses outright: a GIF's frames each depend on the one before, so
		// taking a piece out of one would mean rebuilding every frame. Compressing it works. Swapping
		// the verbs here would take away the one that works and offer the one that never can.
		const ids = verbsFor({ count: 1, subject: AN_ANIMATION }).map((verb) => verb.id);
		expect(ids).toContain('compress');
		expect(ids).not.toContain('edit');
	});

	it('greys Edit with its reason where Sift may not write, and leaves it out for a guest', () => {
		// The copy lands beside the original, which is a write into somebody's library.
		const edit = verbsFor({ count: 1, subject: A_PHOTOGRAPH, canCompress: false }).find(
			(verb) => verb.id === 'edit'
		);
		expect(edit?.disabled).toBe(true);
		expect(edit?.why).toBe(NO_WRITABLE_FOLDER);
		expect(
			verbsFor({ count: 1, subject: A_PHOTOGRAPH, isAdmin: false }).map((verb) => verb.id)
		).not.toContain('edit');
	});

	it('keeps Edit off the bar even if it is somehow declared there', () => {
		// It means nothing pointed at a set, and it says so rather than being quietly filtered.
		const all = verbsFor({ count: 1, subject: A_PHOTOGRAPH });
		expect(all.find((verb) => verb.id === 'edit')?.singleOnly).toBe(true);
		expect(barVerbs(all).map((verb) => verb.id)).not.toContain('edit');
	});
});

describe('making a GIF, a row of its own beside Trim', () => {
	/* The editor's third answer, on a row of its own: reachable only by opening Trim and finding
	   the switch inside the panel, the one thing on that menu somebody comes looking for by name
	   would have no name on the menu. */
	const A_CLIP = { media_type: 'video', favorite: false, concealed: false };
	const A_PHOTOGRAPH = { media_type: 'image', favorite: false, concealed: false };
	const AN_ANIMATION = { media_type: 'gif', favorite: false, concealed: false };

	it('is offered beside Trim, over one video', () => {
		const all = verbsFor({ count: 1, subject: A_CLIP });
		expect(all.map((verb) => verb.id)).toContain('gif');
		expect(all.find((verb) => verb.id === 'gif')?.label).toBe('Create GIF');
		expect(all.find((verb) => verb.id === 'edit')?.label).toBe('Trim');
	});

	it('is offered nowhere Trim is not', () => {
		// The same four conditions, word for word, because it IS Trim's panel: an admin, somewhere
		// Sift may write, one file, and a video. A photograph has no stretch of time to animate and
		// a GIF is the one kind the editor cannot cut at all.
		for (const context of [
			{ count: 12, subject: A_CLIP },
			{ count: 1, subject: A_PHOTOGRAPH },
			{ count: 1, subject: AN_ANIMATION },
			{ count: 1, subject: null },
			{ count: 1, subject: A_CLIP, isAdmin: false }
		]) {
			expect(
				verbsFor(context).map((verb) => verb.id),
				JSON.stringify(context)
			).not.toContain('gif');
		}
	});

	it('is drawn greyed with its reason where Sift may not write, as Trim is', () => {
		const gif = verbsFor({ count: 1, subject: A_CLIP, canCompress: false }).find(
			(verb) => verb.id === 'gif'
		);
		expect(gif?.disabled).toBe(true);
		expect(gif?.why).toBe(NO_WRITABLE_FOLDER);
	});

	it('is absent where the surface cannot open the editor in a mode', () => {
		/* An OPTIONAL handler, the same mechanism the pin uses: a surface offers the row by handing
		   one in. A row declared everywhere and wired nowhere is a dead end drawn on every video. */
		const { gif: _gif, ...without } = HANDLERS;
		expect(
			verbsFor({ count: 1, subject: A_CLIP, handlers: without }).map((verb) => verb.id)
		).not.toContain('gif');
	});

	it('stays off the bar, which never addresses one file in particular', () => {
		const all = verbsFor({ count: 1, subject: A_CLIP });
		expect(all.find((verb) => verb.id === 'gif')?.singleOnly).toBe(true);
		expect(barVerbs(all).map((verb) => verb.id)).not.toContain('gif');
	});
});

describe('the files similar to this one, a way onwards from one file', () => {
	const A_CLIP = { media_type: 'video', favorite: false, concealed: false };
	const A_LOCKED_TILE = { media_type: 'video', favorite: false, concealed: true };
	const OFFERING = { ...HANDLERS, similar: NOTHING };

	it('is offered on one file under the strip name, as a place to go', () => {
		const row = verbsFor({ count: 1, subject: A_CLIP, handlers: OFFERING }).find(
			(verb) => verb.id === 'similar'
		);
		expect(row?.label).toBe('Similar to this');
		expect(row?.group).toBe('open');
		expect(row?.icon).toBe('image_search');
	});

	it('stays off the bar, which never addresses one file in particular', () => {
		const all = verbsFor({ count: 1, subject: A_CLIP, handlers: OFFERING });
		expect(all.find((verb) => verb.id === 'similar')?.singleOnly).toBe(true);
		expect(barVerbs(all).map((verb) => verb.id)).not.toContain('similar');
	});

	it('is absent on a locked tile, and where the surface cannot leave for a wall', () => {
		expect(
			verbsFor({ count: 1, subject: A_LOCKED_TILE, handlers: OFFERING }).map((verb) => verb.id)
		).not.toContain('similar');
		expect(verbsFor({ count: 1, subject: A_CLIP }).map((verb) => verb.id)).not.toContain('similar');
	});
});

/* The file half of the same rule, and the half that carries the INHERITANCE: a file can be refused
   by the Site, the person or the tag it is filed under while its own switch says nothing. */
describe('a file nothing may be asked about', () => {
	it('greys Enrich and says why, and leaves the switch reading what it would do', () => {
		const verbs = verbsFor({
			enrichRefused: true,
			enrichWhy: "Kept local by something it's filed under",
			keptLocal: false,
			handlers: { ...HANDLERS, keepLocal: NOTHING }
		});
		for (const id of ['auto-enrich', 'enrich']) {
			const enrich = verbs.find((verb) => verb.id === id);
			expect(enrich?.disabled, id).toBe(true);
			expect(enrich?.why, id).toBe("Kept local by something it's filed under");
		}
		// Its OWN switch is off, so the row offers to turn it on: pressing it would change this
		// file's row and change nothing about what leaves, which is why the two are separate facts.
		expect(verbs.find((verb) => verb.id === 'keep-local')?.label).toBe("Don't enrich");
	});

	it('takes the box flyout away with it', () => {
		const verbs = verbsFor({
			enrichRefused: true,
			enrichBoxes: [{ word: 'stashdb', name: 'StashDB' }]
		});
		expect(verbs.find((verb) => verb.id === 'auto-enrich')?.children).toBeUndefined();
	});

	it('leaves the row pressable where nothing refuses it', () => {
		const verbs = verbsFor();
		expect(verbs.find((verb) => verb.id === 'enrich')?.disabled).toBeUndefined();
		expect(verbs.find((verb) => verb.id === 'auto-enrich')?.disabled).toBeUndefined();
	});
});

/*
 * AUTO-ENRICH, ENRICH AND DO NOT ENRICH on a file, as on every other surface. The flyout's row is
 * the box that is asked, so each row must hand its own box on, or every per-box row asks every
 * box.
 */
describe('enriching files', () => {
	const BOXES = [
		{ word: 'stashdb', name: 'StashDB' },
		{ word: 'fansdb', name: 'FansDB' }
	];

	it('hands each flyout row its own box, and "All stash-boxes" says every box', () => {
		const asked: (string | undefined)[] = [];
		const verbs = verbsFor({
			enrichBoxes: BOXES,
			handlers: { ...HANDLERS, autoEnrich: (_ids, box) => asked.push(box) }
		});
		const rows = verbs.find((verb) => verb.id === 'auto-enrich')?.children ?? [];
		expect(rows.map((row) => row.label)).toEqual(['All stash-boxes', 'StashDB', 'FansDB']);
		for (const row of rows) row.run?.(['a1']);
		expect(asked).toEqual([EVERY_BOX, 'stashdb', 'fansdb']);
	});

	it('asks what Settings says with no list, which is a press naming no box', () => {
		const asked: (string | undefined)[] = [];
		const verbs = verbsFor({
			handlers: { ...HANDLERS, autoEnrich: (_ids, box) => asked.push(box) }
		});
		verbs.find((verb) => verb.id === 'auto-enrich')?.run?.(['a1']);
		expect(asked).toEqual([undefined]);
	});

	it('offers AcoustID under Auto-enrich beside the boxes, and its press asks AcoustID alone', () => {
		/* The song lookup is one more source Auto-enrich asks. Its row is the server's name and the
		   Songs page's glyph, and it presses its own route: a stash-box press must not run it, and
		   it must not run a stash-box. */
		const boxes: (string | undefined)[] = [];
		const looked: string[][] = [];
		const verbs = verbsFor({
			enrichBoxes: BOXES,
			songLookup: { word: 'acoustid', name: 'AcoustID' },
			handlers: {
				...HANDLERS,
				autoEnrich: (_ids, box) => boxes.push(box),
				lookUpSongs: (ids) => looked.push(ids)
			}
		});
		const rows = verbs.find((verb) => verb.id === 'auto-enrich')?.children ?? [];
		expect(rows.map((row) => row.label)).toEqual([
			'All stash-boxes',
			'StashDB',
			'FansDB',
			'AcoustID'
		]);
		const acoustid = rows.find((row) => row.label === 'AcoustID');
		expect(acoustid?.icon).toBe('music_note_2');
		acoustid?.run?.(['a1', 'a2']);
		expect(looked).toEqual([['a1', 'a2']]);
		expect(boxes).toEqual([]);
	});

	it('offers Ask AcoustID again right under AcoustID, and its press asks again', () => {
		/* A file AcoustID did not know is asked again from its own menu, at any age. The row is
		   drawn only where the screen hands the press over, beside the lookup's own row. */
		const again: string[][] = [];
		const verbs = verbsFor({
			songLookup: { word: 'acoustid', name: 'AcoustID' },
			handlers: {
				...HANDLERS,
				lookUpSongs: NOTHING,
				lookUpSongsAgain: (ids) => again.push(ids)
			}
		});
		const rows = verbs.find((verb) => verb.id === 'auto-enrich')?.children ?? [];
		expect(rows.map((row) => row.label)).toEqual([
			'All stash-boxes',
			'AcoustID',
			'Ask AcoustID again'
		]);
		rows.find((row) => row.label === 'Ask AcoustID again')?.run?.(['a1']);
		expect(again).toEqual([['a1']]);
	});

	it('keeps the stash-boxes a row of their own beside AcoustID when no box is listed', () => {
		const verbs = verbsFor({
			songLookup: { word: 'acoustid', name: 'AcoustID' },
			handlers: { ...HANDLERS, lookUpSongs: NOTHING }
		});
		const rows = verbs.find((verb) => verb.id === 'auto-enrich')?.children ?? [];
		expect(rows.map((row) => row.label)).toEqual(['All stash-boxes', 'AcoustID']);
	});

	it('draws no AcoustID row where the screen has no press for it, or the server named none', () => {
		const without = verbsFor({
			enrichBoxes: BOXES,
			songLookup: { word: 'acoustid', name: 'AcoustID' }
		});
		const rows = without.find((verb) => verb.id === 'auto-enrich')?.children ?? [];
		expect(rows.map((row) => row.label)).not.toContain('AcoustID');
		const unnamed = verbsFor({ handlers: { ...HANDLERS, lookUpSongs: NOTHING } });
		expect(unnamed.find((verb) => verb.id === 'auto-enrich')?.children).toBeUndefined();
	});

	it('names Auto-enrich and Enrich on the bar, never behind the three dots', () => {
		const named = barShape(verbsFor({ enrichBoxes: BOXES })).named.map((verb) => verb.id);
		expect(named).toContain('auto-enrich');
		expect(named).toContain('enrich');
		expect(named.indexOf('add')).toBeLessThan(named.indexOf('auto-enrich'));
	});
});

describe('Run task: the Importing stages, narrowed to what was picked', () => {
	/* What the server lists: Scan's reading, and one pass under each of the other two stages, each
	   stage with its every-pass press as the server names it. */
	const every = (family: string, label: string) => ({
		key: `${family}:all`,
		label,
		help: 'Every pass below, for these files.'
	});
	const GROUPS = [
		{
			family: 'scan',
			label: 'Scan now',
			every: every('scan', 'Scan all'),
			passes: [{ key: 'details', label: 'File details', help: '' }]
		},
		{
			family: 'generate',
			label: 'Generate now',
			every: every('generate', 'Generate all'),
			passes: [
				{ key: 'thumbnails', label: 'Thumbnails', help: '' },
				{ key: 'previews', label: 'Hover previews', help: '' }
			]
		},
		{
			family: 'identify',
			label: 'Identify now',
			every: every('identify', 'Identify all'),
			passes: []
		}
	];

	it('opens onto each stage by its Settings press, and each stage onto its passes', () => {
		const ran: [string[], string][] = [];
		const verb = runNowVerb(GROUPS, (ids, run) => ran.push([ids, run]));

		expect(verb?.label).toBe('Run task');
		/* A stage with nothing that runs for one file is a door onto nothing, so it is not drawn. */
		expect(verb?.children?.map((stage) => stage.label)).toEqual(['Scan now', 'Generate now']);
		expect(verb?.children?.map((stage) => stage.icon)).toEqual(['split_scene', 'error_med']);
		const thumbnails = verb?.children?.[1].children?.[1];
		expect(thumbnails?.label).toBe('Thumbnails');

		thumbnails?.run?.(['a', 'b']);
		expect(ran).toEqual([[['a', 'b'], 'thumbnails']]);
	});

	it("puts the stage's every-pass press first, and it names the stage for the server to expand", () => {
		const ran: [string[], string][] = [];
		const verb = runNowVerb(GROUPS, (ids, run) => ran.push([ids, run]));

		expect(verb?.children?.map((stage) => stage.children?.map((row) => row.label))).toEqual([
			['Scan all', 'File details'],
			['Generate all', 'Thumbnails', 'Hover previews']
		]);
		/* ONE press, one request: the stage's key, never a request per pass sent from here. */
		verb?.children?.[1].children?.[0].run?.(['a']);
		expect(ran).toEqual([[['a'], 'generate:all']]);
	});

	it('is offered to an admin, on both surfaces, and only where the server listed something', () => {
		const handlers = { ...HANDLERS, runNow: NOTHING };
		expect(verbsFor({ handlers, runGroups: GROUPS }).map((verb) => verb.id)).toContain('run-now');
		expect(verbsFor({ handlers, runGroups: [] }).map((verb) => verb.id)).not.toContain('run-now');
		expect(
			verbsFor({ isAdmin: false, handlers, runGroups: GROUPS }).map((verb) => verb.id)
		).not.toContain('run-now');

		/* Two levels down, and still reachable from the bar: a pass hidden inside a group inside a
		   group is still a thing the bar offers, and the flat reading sees it. */
		const all = verbsFor({ handlers, runGroups: GROUPS });
		expect(flatVerbs(all).map((verb) => verb.id)).toContain('run-now:generate:thumbnails');
		const shape = barShape(all);
		expect(shape.named.map((verb) => verb.id)).toContain('run-now');
		expect(flatVerbs(shape.named).map((verb) => verb.label)).toContain('Generate now Thumbnails');
	});

	it("wears the stage's glyph filled on the every-pass press, and outlined on the passes", () => {
		const verb = runNowVerb(GROUPS, NOTHING);
		/* Every row of a stage keeps that stage's mark, so nothing but the fill tells the press that
		   runs all of them from the ones that run one. The stage row itself stays outlined too. */
		expect(verb?.children?.map((stage) => stage.filled ?? false)).toEqual([false, false]);
		expect(
			verb?.children?.map((stage) =>
				stage.children?.map((row) => [row.label, row.icon, row.filled ?? false])
			)
		).toEqual([
			[
				['Scan all', 'split_scene', true],
				['File details', 'split_scene', false]
			],
			[
				['Generate all', 'error_med', true],
				['Thumbnails', 'error_med', false],
				['Hover previews', 'error_med', false]
			]
		]);
	});

	it('draws nothing at all when no stage has a pass', () => {
		expect(runNowVerb([], NOTHING)).toBeNull();
	});
});

describe('a group inside a group, over a set', () => {
	it('loses a single-only row at every depth, and a group left empty goes with it', () => {
		const deep: Verb[] = [
			{
				id: 'outer',
				label: 'Outer',
				icon: 'add',
				children: [
					{
						id: 'inner',
						label: 'Inner',
						icon: 'add',
						children: [{ id: 'one', label: 'One', icon: 'add', singleOnly: true, run: NOTHING }]
					},
					{ id: 'kept', label: 'Kept', icon: 'add', run: NOTHING }
				]
			}
		];
		const shape = barShape(deep);
		expect(shape.named[0].children?.map((verb) => verb.id)).toEqual(['kept']);
	});
});

describe("the file menu's parts", () => {
	const ids = (groups: Verb[][]) => groups.map((group) => group.map((verb) => verb.id));

	it('draws the file menu in its groups, alphabetical inside each, Delete last and alone', () => {
		expect(ids(menuGroups(verbsFor()))).toEqual([
			['add', 'pin', 'rate'],
			['link', 'save'],
			['compress', 'move', 'rename'],
			['auto-enrich', 'enrich'],
			['hide', 'share', 'visibility'],
			['delete']
		]);
	});

	it('keeps the three enrich rows together, in their sequence rather than the alphabet', () => {
		const groups = menuGroups(verbsFor({ handlers: { ...HANDLERS, keepLocal: NOTHING } }));
		expect(ids(groups)).toContainEqual(['auto-enrich', 'enrich', 'keep-local']);
	});

	it('comes in the same parts, in the same order, as a wall of people', () => {
		/* A file's menu and a wall's menu: the same part first, and every part in the one order.
		   Declared groups are what make them agree, so the order of the parts is read off them. */
		const partsOf = (groups: Verb[][]) =>
			groups.map((group) => (group[0].destructive ? 'danger' : (group[0].group ?? 'none')));
		const wall = partsOf(
			menuGroups(
				entityVerbs({
					isAdmin: true,
					handlers: {
						rename: NOTHING,
						tag: PICK,
						favorite: NOTHING,
						rate: NOTHING,
						share: NOTHING,
						hide: NOTHING,
						merge: NOTHING,
						remove: NOTHING
					}
				})
			)
		);
		const file = partsOf(menuGroups(verbsFor()));
		const order = [...VERB_GROUPS.map((one) => one.group as string), 'danger'];
		for (const parts of [wall, file]) {
			expect(parts.map((part) => order.indexOf(part))).toEqual(
				[...parts.map((part) => order.indexOf(part))].sort((a, b) => a - b)
			);
		}
		expect(wall[0]).toBe(file[0]);
		expect(wall.at(-1)).toBe('danger');
	});
});
