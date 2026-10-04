/*
 * What a verb is, and the one rule that separates the two surfaces that draw them.
 *
 * A verb is something that can be done to what is picked, declared once as data by a screen and
 * drawn by both the selection bar and the right-click menu. `singleOnly` is what separates them:
 * the bar always addresses a set and leaves those out. A verb may hold others (grouping), drawn as
 * the same rows on both surfaces (`barShape`). Nothing here calls a server: every `run` is the
 * screen's, so both surfaces run the same code.
 */

import type { IconName } from '$lib/design/icons';
import type { FrequentKind } from '$lib/search/frequent.svelte';
import { byName } from '$lib/search/name-order';
import type { Choice } from '$lib/components/common/PickDialog.svelte';
import type { BulkWriteDone } from '$lib/library/bulk';
import { phoneWidth } from './phone-width.svelte';

/**
 * The picture at the head of a row in one of those lists, already RESOLVED by the host, which holds
 * the items `EntityCard`'s rule reads, so a list of a thousand rows asks nothing as it scrolls.
 */
export interface ChoicePicture {
	/** Where the picture is. A 404 is ordinary: nothing chosen, or a frame not rendered yet. */
	src: string;
	/** A second address for exactly that window. See `Avatar.instead`. */
	instead?: string | null;
	/** A logo rather than a frame out of a clip, so it is shown whole rather than cropped. */
	mark?: boolean;
}

/**
 * One thing that can be picked, with the picture it is drawn by, since a row is read by its picture
 * first. Optional, because some rows have no cover yet. Extends `Choice`, so a list built for the
 * sheet is one the menu can draw.
 */
export interface PickChoice extends Choice {
	picture?: ChoicePicture;
	/** How many steps in the row is drawn: a tag filed under the tag above it. Absent is none. */
	depth?: number;
	/** What the row is filed under, said beside its name when that is not the row above it. */
	within?: string;
	/**
	 * Why this row cannot be chosen here, said beside its name (in a swap's chooser, a thing kept
	 * local or out of swaps): danger tone, the Don't swap mark, and a press ticks nothing.
	 */
	refused?: string;
	/** A person under twenty confirmed faces: their page's band and the words (`thinChoice`). */
	strength?: { band: string; said: string };
}

/**
 * One page of things that can be picked, and how many more the server is holding back.
 *
 * The filtering is the server's, since filtering a cached store under a drawing cap silently hides
 * what falls past it; `more` is drawn as the last row. Alphabetical, with this account's recent
 * picks put in front on the client (`$lib/search/frequent`).
 */
export interface PickPage {
	/** The page itself, alphabetical, already filtered by what was typed. */
	choices: PickChoice[];
	/** How many more match and did not fit. Zero when the page is all of them. */
	more: number;
}

/**
 * Ask for one page: when the picker opens and on each pause of typing, so it must be safe to call
 * repeatedly; the picker drops any answer that is no longer the current question.
 */
export type PickAsk = (typed: string) => Promise<PickPage>;

/**
 * A verb that is answered by picking one thing out of a list, offered as the list itself.
 *
 * "Add to > Collection" is ONE verb: the bar, the menu and a file's own Add to are doors onto one
 * row that opens into the list, marks what the set is on, stays open, and writes to every file.
 * Everything in it is the HOST's (`FileVerbs`); the declaration knows ids and words only.
 */
export interface VerbPick {
	/** Which record of past picks orders this list. See `$lib/search/frequent`. */
	kind: FrequentKind;
	/** The plural word for what is in it: "collections", "people", "Sites". */
	plural: string;
	/** One page of what can be picked, filtered by what is typed. See `PickAsk`. */
	ask: PickAsk;
	/** Put these files on that one, toast and History included; ANSWERS what landed (`PickLanded`). */
	pick: (ids: string[], choice: PickChoice) => Promise<PickLanded>;
	/**
	 * Take these files off that one. Absent leaves the row adding only, and a picker given
	 * `already` with no `unpick` draws a tick it cannot clear: a shape to avoid.
	 */
	unpick?: (ids: string[], choice: PickChoice) => Promise<PickLanded>;
	/**
	 * Which of this kind the files being acted on are ALREADY on: a question about the SET, asked
	 * when the flyout opens. Absent, rows draw no mark (the header pickers); anything not named is
	 * `none`.
	 */
	already?: (ids: string[]) => Promise<Record<string, OnAlready>>;
	/** Create one under this name and pick it. Absent means this kind is not created from here. */
	create?: (name: string) => Promise<PickChoice | null>;
}

/**
 * How much of what is being acted on is already on one thing: three answers, since a menu over a
 * set has no true yes or no.
 */
export type OnAlready = 'all' | 'some' | 'none';

/**
 * What a pick or an unpick landed as, which is what the row's tick is put back to.
 *
 * The tick moves at the press and the answer settles it, so a refusal does not leave the row
 * wrong. `landed`: every file went. `refused`: none went, the tick goes back. `partly`: the picker
 * asks again what the set is on.
 */
export type PickLanded = 'landed' | 'partly' | 'refused';

/**
 * What a bulk write landed as, from the server's own answer: `null` for a write that threw. Read
 * from `skipped` (a file already on the thing is not skipped), never `changed`, which is zero for a
 * tag every file already carries.
 */
export function landedOf(done: BulkWriteDone | null, asked: number): PickLanded {
	if (done === null) return 'refused';
	if (done.skipped <= 0) return 'landed';
	return done.skipped >= asked ? 'refused' : 'partly';
}

/**
 * Which part of a menu a verb belongs in. A menu draws its groups in the order below, with a line
 * between one group and the next and never inside one.
 *
 * - `open`: go to it, or to what it belongs to.
 * - `file`: put it on something, or say what this account thinks of it (Add to, Tag, the heart,
 *   the pin, the stars, a name on a face).
 * - `keep`: take a copy out of Sift (Copy link, Save).
 * - `change`: work on the thing itself (Rename, Move, Trim, Compress, Merge, Run task).
 * - `enrich`: ask the stash-boxes about it, and the row that refuses them.
 * - `share`: who sees it (Hide, Share, Visibility).
 *
 * There is no danger group: a `destructive` verb is always drawn last, alone, below its own line.
 */
export type VerbGroup = 'open' | 'file' | 'keep' | 'change' | 'enrich' | 'share';

/**
 * The groups in the order a menu draws them, and how the rows inside each one are ordered:
 * alphabetical, except `enrich`, whose rows are a sequence ending in the refusal.
 */
export const VERB_GROUPS: readonly { group: VerbGroup; arranged: boolean }[] = [
	{ group: 'open', arranged: false },
	{ group: 'file', arranged: false },
	{ group: 'keep', arranged: false },
	{ group: 'change', arranged: false },
	{ group: 'enrich', arranged: true },
	{ group: 'share', arranged: false }
];

export interface Verb {
	/** What it is called in code. Stable, and what a drift test names it by. */
	id: string;
	/**
	 * Which part of the menu it is drawn in. See `VerbGroup`. A list with no group is drawn as
	 * declared (`check_menu_groups.js` refuses that past five rows); an ungrouped row in a grouped
	 * list is drawn first.
	 */
	group?: VerbGroup;
	/** What it says on screen. Worked out once, so the two surfaces cannot word it differently. */
	label: string;
	icon: IconName;
	/** Draw the glyph solid. For the ones whose outline is mostly empty space. */
	filled?: boolean;
	/** Red, and the weight that goes with it. Only for the one that destroys something. */
	destructive?: boolean;
	/**
	 * Offered, and not answerable just now: drawn dim and inert on both surfaces, with `why`, rather
	 * than vanishing under a reaching pointer. Not for "this wall cannot do that": hand no handler
	 * and the verb is not built.
	 */
	disabled?: boolean;
	/**
	 * Why it cannot be answered, in a few words, shown as a tooltip: written once beside the verb so
	 * both surfaces give the same reason. Read only while `disabled`.
	 */
	why?: string;
	/** A row of stars rather than a press: a rating is one of six answers, handed straight back. */
	stars?: boolean;
	/** The rating showing now, when everything being acted on agrees on one. Null is unrated. */
	rating?: number | null;
	/**
	 * Draw the stars behind a press rather than in the row itself: on files, live stars in a bar of
	 * worded buttons read as decoration. The entity walls keep them inline, where they were fine.
	 */
	flyout?: boolean;
	/**
	 * Keep this one named in the bar, rather than behind its three dots, so the bar names the few
	 * verbs anybody reaches for. A property, not a position, so inserting a verb cannot shift it.
	 * `stars`, `destructive` and a verb with `children` are named by nature (`barShape`).
	 */
	primary?: boolean;
	/**
	 * A second line under the row about the SUBJECT ("Last: 16 Sep 2026, 4:12 PM" under Enrich),
	 * declared beside the words so both surfaces say it alike. Absent draws nothing.
	 */
	note?: string;
	/**
	 * Meaningless pointed at a set (renaming forty things to one name), so the bar does not offer
	 * it: the ONLY reason a verb may be on one surface and not the other.
	 */
	singleOnly?: boolean;
	/**
	 * Rows that hang under this one, instead of this one being something you can do: never with a
	 * `run`, or the row would act on a hover. The child's label is the object and the parent's the
	 * verb phrase, which the bar joins, so each half is written once.
	 */
	children?: readonly Verb[];
	/**
	 * This row already reads as a complete instruction, so the flat surface does not join it to its
	 * group's phrase (the favorite verb reversed: "Remove from favorites").
	 */
	alone?: boolean;
	/**
	 * Answered by picking one thing out of a list, drawn as the list itself wherever the row is drawn
	 * (`VerbMenuItems`, `VerbPick`). Needs no `run`; where both exist, every renderer reads this.
	 */
	pick?: VerbPick;
	/** Do it, to these. Absent on a stars verb, which answers with a value instead, and on a verb
	 *  that holds children, which is a place to look rather than a thing to do. */
	run?: (ids: string[]) => void;
	/** Set the rating on these. Only on the stars verb. */
	rate?: (ids: string[], rating: number | null) => void;
}

/**
 * Every verb as something that can be DONE, with a grouped one replaced by what it holds, so no
 * verb hides from the bar under a group. Labels are joined ("Add to" + "Person"), the words
 * somebody would have walked.
 */
export function flatVerbs(verbs: readonly Verb[]): Verb[] {
	/* All the way down, since groups nest ("Run task" > "Generate now" > the passes). */
	return verbs.flatMap((verb) =>
		verb.children === undefined
			? [verb]
			: flatVerbs(
					verb.children.map((child) =>
						child.alone ? { ...child } : { ...child, label: `${verb.label} ${child.label}` }
					)
				)
	);
}

/**
 * The verbs in the groups a menu draws them in: each group a run of rows, in `VERB_GROUPS` order,
 * and every destructive verb last, as a group of its own. Empty groups are left out, so no line
 * has nothing on one side. Only the top level is regrouped; children keep their declared order.
 */
export function menuGroups(verbs: readonly Verb[]): Verb[][] {
	const benign = verbs.filter((verb) => !verb.destructive);
	const destroying = verbs.filter((verb) => verb.destructive);
	const groups: Verb[][] = [benign.filter((verb) => verb.group === undefined)];
	for (const { group, arranged } of VERB_GROUPS) {
		const rows = benign.filter((verb) => verb.group === group);
		groups.push(arranged ? rows : [...rows].sort(byLabel));
	}
	groups.push(destroying);
	return groups.filter((rows) => rows.length > 0);
}

/** Alphabetical by what the row says, by the one rule every list of names in the app follows. */
function byLabel(one: Verb, other: Verb): number {
	return byName({ name: one.label }, { name: other.label });
}

/** What the bar offers: everything except the verbs that only mean something pointed at one. In
 *  the menu's order, so the two surfaces list one declaration the same way. */
export function barVerbs(verbs: readonly Verb[]): Verb[] {
	return flatVerbs(menuVerbs(verbs)).filter((verb) => !verb.singleOnly);
}

/** What a menu offers: all of them, in the order its groups draw them. A menu is opened on
 *  something, so it can say Open, and it keeps the grouping, because the column is the surface
 *  grouping exists for. */
export function menuVerbs(verbs: readonly Verb[]): Verb[] {
	return menuGroups(verbs).flat();
}

/** How many verbs a selection bar names at a phone's width. See `barShape`. */
export const PHONE_BAR_NAMED = 4;

/**
 * The bar's two halves: the few it names, and everything else, behind its three dots.
 *
 * The bar renders the declaration's own shape, like the menu, so `bar-and-menu.test.ts` compares
 * them id for id; `singleOnly` is pruned at every depth. At a phone's width the bar names four
 * (`PHONE_BAR_NAMED`), five columns a thumb can hit; the destroying verb keeps its place, since it
 * must never be hidden. `most` is for a test, or a bar that knows better.
 */
export function barShape(
	verbs: readonly Verb[],
	most: number = phoneWidth.yes ? PHONE_BAR_NAMED : Number.POSITIVE_INFINITY
): { named: Verb[]; rest: Verb[] } {
	const rows = overASet(menuVerbs(verbs));
	const stays = (verb: Verb): boolean =>
		verb.primary === true ||
		verb.stars === true ||
		verb.destructive === true ||
		verb.children !== undefined;
	const named = rows.filter(stays);
	const kept = keepTheMost(named, most);
	return {
		named: kept,
		rest: rows.filter((verb) => !kept.includes(verb))
	};
}

/* The first `most` of the named verbs, the destroying ones kept first. In the order they came. */
function keepTheMost(named: readonly Verb[], most: number): Verb[] {
	if (named.length <= most) return [...named];
	const destroying = named.filter((verb) => verb.destructive === true).slice(0, most);
	let room = most - destroying.length;
	return named.filter((verb) => {
		if (verb.destructive === true) return destroying.includes(verb);
		if (room <= 0) return false;
		room -= 1;
		return true;
	});
}

/**
 * These verbs as a SET may be offered them: every `singleOnly` one taken out, at every depth, and a
 * group left with nothing in it taken out with it: a door onto nothing is not a verb.
 */
function overASet(verbs: readonly Verb[]): Verb[] {
	return verbs
		.filter((verb) => !verb.singleOnly)
		.map((verb) =>
			verb.children === undefined ? verb : { ...verb, children: overASet(verb.children) }
		)
		.filter((verb) => verb.children === undefined || verb.children.length > 0);
}

/*
 * THE ROWS A FILE'S MENU AND A WALL'S MENU SHARE, made once for both lists, so a row both carry
 * says the same words in the same place. Each list decides which it offers.
 */

/** The door onto the places a thing can be put, and the first row of both menus. */
export function addToVerb(children: readonly Verb[]): Verb {
	return { id: 'add', label: 'Add to', icon: 'add', group: 'file', primary: true, children };
}

/**
 * The heart, worded for what pressing it will do: "Remove from favorites" once everything is one,
 * "Favorites" inside Add to, "Add to favorites" alone. `alone` either way, so no surface joins
 * "Add to" in front of it.
 */
export function favoriteVerb(
	already: boolean,
	run: (ids: string[]) => void,
	insideAddTo: boolean
): Verb {
	return {
		id: 'favorite',
		label: already ? 'Remove from favorites' : insideAddTo ? 'Favorites' : 'Add to favorites',
		icon: 'favorite',
		group: 'file',
		alone: true,
		primary: true,
		run
	};
}

/**
 * Keeping a thing at the top of its wall, or taking the pin out. The words and the glyph reverse on
 * something already pinned, as Hide's do: a row reading "Pin" on a pinned thing appears to do
 * nothing. It takes what the rows should BECOME, so a mixed pick ends in a state somebody can say.
 */
export function pinVerb(already: boolean, run: (ids: string[], pinned: boolean) => void): Verb {
	return {
		id: 'pin',
		label: already ? 'Unpin' : 'Pin',
		icon: already ? 'keep_off' : 'keep',
		group: 'file',
		alone: true,
		run: (ids) => run(ids, !already)
	};
}

/**
 * The stars, behind a press on both menus: one shape for the verb wherever it appears, rather than
 * five inline stars taking the width of five controls in a crowded bar.
 */
export function ratingVerb(
	rating: number | null,
	rate: ((ids: string[], rating: number | null) => void) | undefined
): Verb {
	return {
		id: 'rate',
		label: 'Rating',
		icon: 'star',
		group: 'file',
		stars: true,
		flyout: true,
		rating,
		...(rate ? { rate } : {})
	};
}
