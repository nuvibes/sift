/* Which component draws which queue.
 *
 * The client half of the same arrangement the server has: the workbench lists what registered and
 * knows nothing about what any of it holds, and this is the one place that says how to draw one.
 *
 * A panel arriving in a later version is a line here and a component beside it. Nothing else in the
 * workbench changes: not the board, not the queue screen, not the address it lives at.
 *
 * A queue the server offers and this has no drawing for is not a fault and is not a blank screen:
 * the card still says what it is and how much of it there is, and it simply cannot be opened. That
 * is what an older client meeting a newer server looks like, and it is the honest way round.
 */

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

/* The two names an item's address is built from, and the addresses themselves, live in a module
   that imports no component: every panel that links to a pile or a chain imports them, and this
   file imports every panel. See `addresses.ts`. */
import { DUPLICATES, TO_NAME } from './addresses';
import { WIDER_WINDOW_TITLE } from '$lib/shell/wider-window';

/** The names the server uses for the queues it ships with. */
export const FOLDERS = 'folders';
/* The record beside the folder questions: what a pass filed without asking. A tab of the same
   page, drawn as rows rather than cards. See `FiledPanel`. */
const FILED = 'filed';
/* Not exported: the one thing outside this file that would name them builds an item's address,
   and that is `addresses.ts`. A name on the way out of a module is a claim that somebody else
   may depend on it. */
/* What a file's OWN NAME said about where it came from, grouped by the username it named. See
   `FilenamesPanel` for why the browse wall is the wrong screen for a decision the pass took per
   USERNAME. */
const FILENAMES = 'filenames';
/* The faces: one page with five tabs, each its own queue on the server. The first queue is named
   for the group, so the board's Faces card opens `/organize/faces` rather than a name for a
   state. What each holds is the server's business. See the faces slice's own `queue.py`; all
   this file says is which component draws which. */
const FACES = 'faces';
const DISAGREEMENTS = 'disagreements';
/* The groups somebody discarded. The word on screen is Discard, and the older `ignored-faces`
   address redirects, see `movedTo`. The server names the tab (`SET_ASIDE` in the faces slice's
   `queue.py`) and nothing checks the two spellings against each other, so a rename is made on
   both sides. */
const SET_ASIDE = 'discarded-faces';
const PEOPLE_KNOWN = 'known-people';
/* The queue's name on the server and the pile's address (`/organize/usernames`). The older
   `handles` address redirects, see `movedTo`. */
export const USERNAMES = 'usernames';
const WAS_HANDLES = 'handles';
const TAGGER = 'tagger';
/* The record beside the pile a stash-box recognised: every entity a box has been agreed to
   know. A tab of the same page. See `LinkedPanel`. */
const LINKED = 'linked';
/* And what the enrichment could not decide. See `UndecidedPanel`. */
const UNDECIDED = 'undecided';
/* And the studios a box made Sites of that may be one person's own store. See `StudiosPanel`. */
const STUDIOS = 'studios';
/* Older names for the faces queues. Kept as names rather than deleted because an address
   outlives the screen it opened: they are in links, in bookmarks and in what somebody typed last
   week, and a dead address in this application is a page saying "there is nothing of that name
   here" over a feature that is still there. See `movedTo`. */
const WAS_UNIDENTIFIED = 'unidentified';
const WAS_IGNORED = 'ignored';
const WAS_IDENTIFIED = 'identified';
const WAS_LOOK_ALIKES = 'look-alikes';
/* And the one list the faces queues were once folded into, which the five tabs replace. It was
   the address of the board's Faces card, so it is in exactly the same position as the names
   above: a link somebody has, pointing at a screen that is still here under another name. */
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
	/* One component for two names, because they are one wall of group cards filtered two ways: it
	   reads which of the two it is drawing off the address. Two components would be two copies of
	   the same card with one word different between them. */
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

/**
 * THE CONTACT SHEETS: the queues whose work is looking at a whole group side by side (the copies of
 * one file, the look-alike faces of one person) and deciding about all of it in one go.
 *
 * Not drawn at a phone's width, for the first version the phone has: a group is compared by laying
 * its pictures next to each other, and a phone holds two across, so a group of forty is a scroll
 * that the decision has to be carried down. Their queue says so and the tabs beside it still lead
 * to the queues that ARE one question at a time (Faces to confirm, Disagreements, People Sift
 * knows), which a phone answers well. One item of one of these (a pile, a chain) is the same sheet,
 * so the older names that still open one are here too.
 */
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

/* And how to draw ONE ITEM of a queue, for the queues that have something worth opening.
 *
 * Not every queue does. A folder card is answered where it sits, so opening one would be a screen
 * showing the same question again with more space around it.
 */
const DETAILS: Record<string, Component> = {
	[TO_NAME]: PileDetail,
	[SET_ASIDE]: PileDetail,
	[WAS_TO_CHECK]: PileDetail,
	[PEOPLE_KNOWN]: IdentifiedForPerson,
	/* And the three addresses those two replaced, which are still in links, in bookmarks and in
	   every decision receipt ever written. They redirect at the QUEUE (see `movedTo`); one item of
	   a queue keeps working where it stands, because a redirect that carried an id would have to be
	   right about which of the two screens each old name meant. */
	[WAS_UNIDENTIFIED]: PileDetail,
	[WAS_IGNORED]: PileDetail,
	[WAS_IDENTIFIED]: IdentifiedForPerson,
	/* A chain (a group past the cap, which the queue cannot answer) opened on a screen of its
	   own where every file in it can be looked at. See `ChainDetail`. */
	[DUPLICATES]: ChainDetail,
	/* One proposed shoot opened up: every picture of it as a wall, with the card's question and
	   answers. See `ShootDetail`. */
	[SHOOTS]: ShootDetail
};

/**
 * Where a queue that has been renamed lives now, or null for one that has not.
 *
 * Older queue names are still in links, in bookmarks and in whatever somebody has open in another
 * tab. A queue this build has never heard of draws "There is nothing of that name here", which is
 * the honest answer for a queue from another version and a wrong one for a screen that is still
 * here under a new name.
 *
 * Held here beside the registry rather than in the route, because this file is the one place that
 * says which name draws which screen: a second list living in a page would be a second thing to
 * edit the next time one moves, and it would be the one nobody remembers.
 */
export function movedTo(queue: string | undefined): string | null {
	/* Each old name goes to the tab that holds what that name held, rather than all of them to the
	   front of the group: a link into the ignored groups that landed on the suggestions would be a
	   redirect that works and is wrong, which is worse than one that says nothing is there.
	   `to-check` is the exception and it goes to the group's own page: it WAS all three, so there
	   is no one tab it meant, and that page is where those tabs are. */
	if (queue === WAS_UNIDENTIFIED) return TO_NAME;
	if (queue === WAS_HANDLES) return USERNAMES;
	if (queue === WAS_IGNORED || queue === WAS_IGNORED_FACES) return SET_ASIDE;
	if (queue === WAS_LOOK_ALIKES || queue === WAS_TO_CHECK) return FACES;
	return queue === WAS_IDENTIFIED ? PEOPLE_KNOWN : null;
}

/**
 * Where one ITEM of a moved queue goes: the same map as `movedTo`, except for `to-check`.
 *
 * The queue `to-check` goes to the first queue's page because it WAS all three tabs. An item of it
 * is not three things: it is one pile, and the tab that opens a pile by its id is To name. Sending
 * the item where the queue goes would land it on `/organize/faces/<id>`, which has no item screen
 * at all: every face-group link written into a receipt under that name would open "There is
 * nothing of that name here".
 */
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

/**
 * Where one queue's card goes when it is pressed, or null where it goes nowhere.
 *
 * **Not `canOpen` alone, which answers a different question.** "Can this build draw the panel" is
 * the same answer as "is the card a link" for a queue whose way in is its panel, and wrong for one
 * whose way in is somewhere else and that has no panel: a card decided by `canOpen` would have a
 * disabled heading button and a dead body, with the card, the title and the heading all reaching
 * nothing.
 *
 * The server answers it (`QueueView.opens`) for the same reason it answers the icon, the band
 * and every preview address: only the area that registered a queue knows where its own things live,
 * and a list of queue-name-to-address living here would be a second place to edit every time one is
 * added. That is the whole arrangement this file's header describes, and the map above is the one
 * thing that genuinely cannot move (a component is code).
 *
 * The declared address wins over the panel. Nothing declares both today; the day something does,
 * the server saying where its card goes is the answer and an older client's panel is not.
 */
export function wayIn(queue: { name: string; opens?: string | null }): string | null {
	if (queue.opens) return queue.opens;
	return canOpen(queue.name) ? `/organize/${queue.name}` : null;
}
