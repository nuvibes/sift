/* Which component draws which queue. */

import type { Component } from 'svelte';

import PileDetail from '$lib/components/faces/PileDetail.svelte';
import SuggestionsPanel from '$lib/components/organize/SuggestionsPanel.svelte';
import DisagreementsPanel from '$lib/components/organize/DisagreementsPanel.svelte';
import FaceGroupsPanel from '$lib/components/organize/FaceGroupsPanel.svelte';
import ChainDetail from '$lib/components/organize/ChainDetail.svelte';
import ShootDetail from '$lib/components/organize/ShootDetail.svelte';
import IdentifiedForPerson from '$lib/components/organize/IdentifiedForPerson.svelte';
import FiledPanel from '$lib/components/organize/FiledPanel.svelte';
import FilenamesPanel from '$lib/components/organize/FilenamesPanel.svelte';
import FolderPanel from '$lib/components/organize/FolderPanel.svelte';
import IdentifiedPanel from '$lib/components/organize/IdentifiedPanel.svelte';
import CopiesPanel from '$lib/components/organize/CopiesPanel.svelte';
import DuplicatesPanel from '$lib/components/organize/DuplicatesPanel.svelte';
import UsernamePanel from '$lib/components/organize/UsernamePanel.svelte';
import QuarantinePanel from '$lib/components/organize/QuarantinePanel.svelte';
import ShootsPanel from '$lib/components/organize/ShootsPanel.svelte';
import SkippedPanel from '$lib/components/organize/SkippedPanel.svelte';
import TaggerPanel from '$lib/components/organize/TaggerPanel.svelte';
import LinkedPanel from '$lib/components/organize/LinkedPanel.svelte';
import UndecidedPanel from '$lib/components/organize/UndecidedPanel.svelte';
import StudiosPanel from '$lib/components/organize/StudiosPanel.svelte';

/* The names an item's address is built from live in a module that imports no component. */
import { DUPLICATES, TO_NAME } from './addresses';
import { WIDER_WINDOW_TITLE } from '$lib/shell/wider-window';

/** The names the server uses for the queues it ships with. */
export const FOLDERS = 'folders';
/* The record beside the folder questions: what a pass filed without asking. */
const FILED = 'filed';
/* Not exported: the one thing outside this file that would name them is `addresses.ts`. */
/* What a file's OWN NAME said about where it came from, grouped by the username it named. */
const FILENAMES = 'filenames';
/* The faces: one page with five tabs, each its own queue on the server. */
const FACES = 'faces';
const DISAGREEMENTS = 'disagreements';
/* The groups somebody discarded. The older `ignored-faces` address redirects (`movedTo`). */
const SET_ASIDE = 'discarded-faces';
const PEOPLE_KNOWN = 'known-people';
/* The queue's name on the server and the pile's address (`/organize/usernames`). */
export const USERNAMES = 'usernames';
const WAS_HANDLES = 'handles';
const TAGGER = 'tagger';
/* The record beside the pile a stash-box recognised: every entity a box has been agreed to know. */
const LINKED = 'linked';
/* And what the enrichment could not decide. See `UndecidedPanel`. */
const UNDECIDED = 'undecided';
/* And the studios a box made Sites of that may be one person's own store. See `StudiosPanel`. */
const STUDIOS = 'studios';
/* Older names for the faces queues. */
const WAS_UNIDENTIFIED = 'unidentified';
const WAS_IGNORED = 'ignored';
const WAS_IDENTIFIED = 'identified';
const WAS_LOOK_ALIKES = 'look-alikes';
/* And the one list the faces queues were once folded into, which the five tabs replace. */
const WAS_TO_CHECK = 'to-check';
/* The tab's own older address, from before the word changed. */
const WAS_IGNORED_FACES = 'ignored-faces';

const COPIES = 'copies';
/* Runs of one creator's loose pictures that the meaning index puts in one sitting. */
const SHOOTS = 'shoots';
const QUARANTINE = 'quarantine';
const SKIPPED = 'skipped';

const PANELS: Record<string, Component> = {
	[FOLDERS]: FolderPanel,
	[FILED]: FiledPanel,
	[FILENAMES]: FilenamesPanel,
	[FACES]: SuggestionsPanel,
	[DISAGREEMENTS]: DisagreementsPanel,
	/* One component for two names: one wall of group cards, filtered by the address. */
	[TO_NAME]: FaceGroupsPanel,
	[SET_ASIDE]: FaceGroupsPanel,
	[PEOPLE_KNOWN]: IdentifiedPanel,
	[USERNAMES]: UsernamePanel,
	[TAGGER]: TaggerPanel,
	[LINKED]: LinkedPanel,
	[UNDECIDED]: UndecidedPanel,
	[STUDIOS]: StudiosPanel,
	[DUPLICATES]: DuplicatesPanel,
	[COPIES]: CopiesPanel,
	[SHOOTS]: ShootsPanel,
	[QUARANTINE]: QuarantinePanel,
	[SKIPPED]: SkippedPanel
};

/** THE CONTACT SHEETS: the queues whose work is deciding about a whole group side by side. */
const WIDE_ONLY: ReadonlySet<string> = new Set([
	DUPLICATES,
	COPIES,
	TO_NAME,
	SET_ASIDE,
	WAS_UNIDENTIFIED,
	WAS_IGNORED,
	WAS_TO_CHECK
]);

/** Whether a queue (or one item of it) is a contact sheet, which a phone's width does not draw. */
export function needsWiderWindow(queue: string): boolean {
	return WIDE_ONLY.has(queue);
}

/** What a contact sheet says on a phone instead, in the words Theater's own refusal uses. */
export const WIDER_WINDOW = {
	title: WIDER_WINDOW_TITLE,
	body: "Deciding about a whole group side by side doesn't fit on a screen this size. Open it on a computer or a tablet."
};

/* And how to draw ONE ITEM of a queue, for the queues that have something worth opening. */
const DETAILS: Record<string, Component> = {
	[TO_NAME]: PileDetail,
	[SET_ASIDE]: PileDetail,
	[WAS_TO_CHECK]: PileDetail,
	[PEOPLE_KNOWN]: IdentifiedForPerson,
	/* And the three addresses those two replaced, still in links, bookmarks and decision
	   receipts. */
	[WAS_UNIDENTIFIED]: PileDetail,
	[WAS_IGNORED]: PileDetail,
	[WAS_IDENTIFIED]: IdentifiedForPerson,
	/* A chain (a group past the cap) opened on a screen of its own. */
	[DUPLICATES]: ChainDetail,
	/* One proposed shoot opened up: every picture of it as a wall, with the card's question. */
	[SHOOTS]: ShootDetail
};

/** Where a queue that has been renamed lives now, or null for one that has not. */
export function movedTo(queue: string | undefined): string | null {
	/* Each old name goes to the tab that holds what that name held, not to the front of the
	   group. */
	if (queue === WAS_UNIDENTIFIED) return TO_NAME;
	if (queue === WAS_HANDLES) return USERNAMES;
	if (queue === WAS_IGNORED || queue === WAS_IGNORED_FACES) return SET_ASIDE;
	if (queue === WAS_LOOK_ALIKES || queue === WAS_TO_CHECK) return FACES;
	return queue === WAS_IDENTIFIED ? PEOPLE_KNOWN : null;
}

/** Where one ITEM of a moved queue goes: the same map as `movedTo`, except for `to-check`. */
export function itemMovedTo(queue: string | undefined): string | null {
	return queue === WAS_TO_CHECK ? TO_NAME : movedTo(queue);
}

/** How to draw one queue, or nothing when this version has no drawing for it. */
export function panelFor(queue: string): Component | undefined {
	return PANELS[queue];
}

/** How to draw one item of a queue, or nothing when that queue has no detail screen. */
export function detailFor(queue: string): Component | undefined {
	return DETAILS[queue];
}

/** Whether this version can DRAW a queue's own panel. Not the same question as `wayIn` below. */
export function canOpen(queue: string): boolean {
	return queue in PANELS;
}

/** Where one queue's card goes when it is pressed, or null where it goes nowhere. */
export function wayIn(queue: { name: string; opens?: string | null }): string | null {
	if (queue.opens) return queue.opens;
	return canOpen(queue.name) ? `/organize/${queue.name}` : null;
}
