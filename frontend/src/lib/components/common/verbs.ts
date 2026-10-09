/* What a verb is: declared once as data, drawn by the selection bar and the right-click menu. */

import type { IconName } from '$lib/design/icons';
import type { FrequentKind } from '$lib/search/frequent.svelte';
import { byName } from '$lib/search/name-order';
import type { Choice } from '$lib/components/common/PickDialog.svelte';
import type { BulkWriteDone } from '$lib/library/bulk';
import { phoneWidth } from './phone-width.svelte';

/** A list row's picture, resolved by the host so a long list asks nothing as it scrolls. */
export interface ChoicePicture {
	/** Where the picture is. A 404 is ordinary: nothing chosen, or a frame not rendered yet. */
	src: string;
	/** A second address for exactly that window. See `Avatar.instead`. */
	instead?: string | null;
	/** A logo rather than a frame out of a clip, so it is shown whole rather than cropped. */
	mark?: boolean;
}

/** One thing that can be picked, with the picture its row is read by. */
export interface PickChoice extends Choice {
	picture?: ChoicePicture;
	/** How many steps in the row is drawn: a tag filed under the tag above it. Absent is none. */
	depth?: number;
	/** What the row is filed under, said beside its name when that is not the row above it. */
	within?: string;
	/** Why this row cannot be chosen here, said beside its name; a press ticks nothing. */
	refused?: string;
	/** A person under twenty confirmed faces: their page's band and the words (`thinChoice`). */
	strength?: { band: string; said: string };
}

/** One page of choices, filtered by the server, and how many more it holds back. */
export interface PickPage {
	choices: PickChoice[];
	/** How many more match and did not fit. Zero when the page is all of them. */
	more: number;
}

/** Ask for one page; called repeatedly, and a stale answer is dropped. */
export type PickAsk = (typed: string) => Promise<PickPage>;

/** A verb answered by picking one thing from a list, drawn as the list itself. */
export interface VerbPick {
	/** Which record of past picks orders this list. See `$lib/search/frequent`. */
	kind: FrequentKind;
	plural: string;
	ask: PickAsk;
	/** Put these files on that one, toast and History included; ANSWERS what landed (`PickLanded`). */
	pick: (ids: string[], choice: PickChoice) => Promise<PickLanded>;
	/** Take these files off that one; without it a tick cannot be cleared. */
	unpick?: (ids: string[], choice: PickChoice) => Promise<PickLanded>;
	/** Which of this kind the set is already on; absent, rows draw no mark. */
	already?: (ids: string[]) => Promise<Record<string, OnAlready>>;
	/** Create one under this name and pick it. Absent means this kind is not created from here. */
	create?: (name: string) => Promise<PickChoice | null>;
}

/** How much of a set is already on one thing: a set has no plain yes or no. */
export type OnAlready = 'all' | 'some' | 'none';

/** What a pick landed as; the tick moves at the press and the answer settles it. */
export type PickLanded = 'landed' | 'partly' | 'refused';

/** A bulk write's landing, read from `skipped`, never `changed` (zero for a tag already on). */
export function landedOf(done: BulkWriteDone | null, asked: number): PickLanded {
	if (done === null) return 'refused';
	if (done.skipped <= 0) return 'landed';
	return done.skipped >= asked ? 'refused' : 'partly';
}

/** Which part of a menu a verb belongs in, drawn in this order with a line between groups;
 * a destructive verb is always last, alone. */
export type VerbGroup = 'open' | 'file' | 'keep' | 'change' | 'enrich' | 'share';

/** The groups in drawing order; rows alphabetical except `enrich`, a sequence. */
export const VERB_GROUPS: readonly { group: VerbGroup; arranged: boolean }[] = [
	{ group: 'open', arranged: false },
	{ group: 'file', arranged: false },
	{ group: 'keep', arranged: false },
	{ group: 'change', arranged: false },
	{ group: 'enrich', arranged: true },
	{ group: 'share', arranged: false }
];

export interface Verb {
	id: string;
	/** The menu part it is drawn in; an ungrouped list is drawn as declared. */
	group?: VerbGroup;
	label: string;
	icon: IconName;
	filled?: boolean;
	/** Red, and the weight that goes with it. Only for the one that destroys something. */
	destructive?: boolean;
	/** Offered but not answerable now: drawn dim with `why`, never vanishing. */
	disabled?: boolean;
	/** Why it is disabled, as a tooltip, written once for both surfaces. */
	why?: string;
	/** A row of stars rather than a press: a rating is one of six answers, handed straight back. */
	stars?: boolean;
	rating?: number | null;
	/** Draw the stars behind a press rather than inline in the row. */
	flyout?: boolean;
	/** Keep this one named in the bar, not behind its three dots. */
	primary?: boolean;
	/** A second line about the subject under the row ("Last: ..."). */
	note?: string;
	/** Meaningless over a set, so the bar leaves it out: the only reason for one surface. */
	singleOnly?: boolean;
	/** Rows hanging under this one, never with a `run`; the bar joins the two labels. */
	children?: readonly Verb[];
	/** Already a whole instruction, so it is not joined to its group's phrase. */
	alone?: boolean;
	/** Answered by picking from a list, drawn as the list wherever the row is. */
	pick?: VerbPick;
	/** Do it, to these. Absent on a stars verb and on a group. */
	run?: (ids: string[]) => void;
	rate?: (ids: string[], rating: number | null) => void;
}

/** Every verb as something that can be done, groups replaced by their joined children. */
export function flatVerbs(verbs: readonly Verb[]): Verb[] {
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

/** The verbs in their menu groups, destructive last; only the top level is regrouped. */
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

function byLabel(one: Verb, other: Verb): number {
	return byName({ name: one.label }, { name: other.label });
}

/** What the bar offers: all but the one-thing verbs, in the menu's order. */
export function barVerbs(verbs: readonly Verb[]): Verb[] {
	return flatVerbs(menuVerbs(verbs)).filter((verb) => !verb.singleOnly);
}

export function menuVerbs(verbs: readonly Verb[]): Verb[] {
	return menuGroups(verbs).flat();
}

export const PHONE_BAR_NAMED = 4;

/** The bar's two halves: the named few and the rest behind its dots (bar-and-menu.test.ts). */
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

/** These verbs over a set: `singleOnly` out at every depth, and any group left empty. */
function overASet(verbs: readonly Verb[]): Verb[] {
	return verbs
		.filter((verb) => !verb.singleOnly)
		.map((verb) =>
			verb.children === undefined ? verb : { ...verb, children: overASet(verb.children) }
		)
		.filter((verb) => verb.children === undefined || verb.children.length > 0);
}

export function addToVerb(children: readonly Verb[]): Verb {
	return { id: 'add', label: 'Add to', icon: 'add', group: 'file', primary: true, children };
}

/** The heart, worded for what pressing it will do; `alone` so nothing joins in front. */
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

/** Pin or unpin, the words reversing on a pinned thing; it takes what the rows become. */
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
