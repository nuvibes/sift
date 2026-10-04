/*
 * What the badge in the corner of a thing means, and the words for it.
 *
 * One module because the same mark is drawn in four places (a tile, an entity card, a tag chip, a
 * folder row) and read in a fifth, the legend in Settings. Four copies of "which glyph, which
 * colour, which sentence" is four chances for the legend to describe something the app does not
 * draw, which is worse than having no legend at all.
 *
 * Two facts in one mark, and the whole design follows from keeping them apart:
 *
 *   WHAT the answer is       shared, restricted, or both at once: the glyph and the colour
 *   WHERE THE SWITCH IS      on this thing, or on something above it: solid or hollow
 *
 * SOLID MEANS THIS IS THE THING DOING THE CONTROLLING. HOLLOW MEANS SOMETHING ABOVE IT IS. That is
 * the whole rule, and it is the actionable one: a solid mark says the decision is right here and
 * this is where to change it, a hollow one says go up. A shared folder is solid on its own row and
 * every file inside it is hollow: same decision, and the two marks are saying different true
 * things about where to go for it.
 *
 * Both at once is not a contradiction and it is not an error. The two flags are about DIFFERENT
 * ACCOUNTS: one guest can reach a file while another is kept from it. Drawn red it would read as
 * "nobody" and drawn in the plain ink as "everybody", so it has a colour of its own.
 */

import type { IconName } from '$lib/design/icons';
import type { components } from '$lib/api/schema';

/**
 * What a mark's tooltip ends with, because a mark that can be pressed says so.
 *
 * Saying only where the decision came from ("set on something it is in") is vague about the one
 * thing somebody wants (which folder) and silent about the thing they can do. The panel names the
 * folder; this says how to get there. Not in `words`: an accessible name belongs to a button that
 * already says what pressing it opens, and a mark drawn as text has nothing to press.
 */
export const CLICK_THROUGH = 'Press to see what decides this.';

/* What each answer means, as the tooltip's sentence after the status word. */
const SHARED_WORDS = 'guests you chose can see this';
const RESTRICTED_WORDS = 'guests you chose can never see this';
const BOTH_WORDS = 'shared with some guests and restricted from others';

/* The same answers as a name a screen reader says, which starts with the answer itself. */
const SHARED_NAME = 'Shared with guests you chose';
const RESTRICTED_NAME = 'Restricted from guests you chose';
const BOTH_NAME = 'Shared with some guests and restricted from others';

/* Where the switch is, in the words a person uses for it. */
const SET_HERE = 'set here';
const SET_ABOVE = 'set on something it is in';

/*
 * Hidden, split the way the other three are.
 *
 * The status word is kept apart from the sentence explaining it so that whatever draws it can
 * colour the word alone: a whole line in colour reads as an alarm, one word in it reads as a
 * fact. `HIDDEN_WORDS` is both halves for the places that need one string, which is what an
 * accessible name has to be.
 */
export const HIDDEN_VERB = 'Hidden';
export const HIDDEN_REST = '\u2014 visible to you only when you unhide with your PIN';
export const HIDDEN_WORDS = `${HIDDEN_VERB} ${HIDDEN_REST}`;

/**
 * The same fact as a sentence that stands on its own.
 *
 * `HIDDEN_REST` opens with a dash because it is the tail of "Hidden \u2014 ...", and a dash at the start
 * of its own line is a bullet for a list of one.
 */
export const HIDDEN_EXPLAINS = 'Visible to you only when you unhide with your PIN.';

type MarkKind = 'shared' | 'restricted' | 'both';

export interface Mark {
	kind: MarkKind;
	icon: IconName;
	/** Whether the decision was made on this thing itself. */
	here: boolean;
	/** How to draw it: solid where the switch is on this thing, hollow where it is above it. */
	filled: boolean;
	/** The status word, which carries its own colour wherever it is drawn. */
	verb: string;
	/** The rest of the tooltip's sentence, in ordinary ink, ending with what a press does. */
	rest: string;
	/** The mark as one plain sentence, for an accessible name, which cannot be two elements. */
	words: string;
}

type MarkFacts = Partial<
	Pick<
		components['schemas']['AssetSummary'],
		'shared' | 'restricted' | 'shared_here' | 'restricted_here'
	>
>;

/**
 * The mark for one thing, or null when nothing has been said about it, which is most of a library.
 *
 * `here` defaults to true for the objects that carry no inheritance at all (the ones handed no
 * `shared_here` / `restricted_here`), because on those the only place a decision can have been
 * made is the object itself.
 */
export function markFor(facts: MarkFacts, { file = false } = {}): Mark | null {
	const shared = facts.shared === true;
	const restricted = facts.restricted === true;
	if (!shared && !restricted) return null;

	// WHERE THE SWITCH IS, read from the facts wherever they say. A file always says (absent is
	// "not here"); so does a folder, which sits in a root and another folder, and a Site, which
	// sits under a network whose share reaches it. A tag, a person and a collection inherit nothing
	// and are handed no where-facts at all, and for those the thing itself is the only place a
	// decision can have been made, so absent means here.
	//
	// A folder and a Site both inherit, and the server says which (`shared_here`): ignoring that
	// would mark a label under a shared network, or a folder under a shared root, as "set here"
	// over a switch that is not there.
	//
	// THE SWITCH BEHIND THE ANSWER DRAWN, not any switch on the file. A restrict is absolute, so a
	// file shared on its own inside a restricted folder is Restricted, and its own share is the
	// decision that LOST. Asking "is anything set here" would call that mark solid and say "set
	// here", sending somebody to a switch that changes nothing while the folder that decides it
	// went unnamed. Both at once is two answers, so either switch counts.
	const sharedHere = facts.shared_here === true;
	const restrictedHere = facts.restricted_here === true;
	const decidedHere =
		shared && restricted ? sharedHere || restrictedHere : restricted ? restrictedHere : sharedHere;
	const saysWhere = file || facts.shared_here !== undefined || facts.restricted_here !== undefined;
	const here = saysWhere ? decidedHere : true;
	const filled = here;

	// Where the switch is, said in words as well as drawn, because a filled-versus-hollow glyph is
	// only legible to somebody who already knows the rule.
	const switchedAt = here ? SET_HERE : SET_ABOVE;

	const built = (
		kind: MarkKind,
		icon: IconName,
		verb: string,
		said: string,
		name: string
	): Mark => ({
		kind,
		icon,
		here,
		filled,
		verb,
		// An em dash between the status word and its sentence, the one mark the words on screen use
		// for a pause (`COUNTS_SEPARATOR` in `entity-counts`).
		rest: `\u2014 ${said}, ${switchedAt}. ${CLICK_THROUGH}`,
		words: `${name}, ${switchedAt}`
	});

	if (shared && restricted)
		return built('both', 'group', 'Shared and restricted', BOTH_WORDS, BOTH_NAME);
	if (restricted)
		return built('restricted', 'group_off', 'Restricted', RESTRICTED_WORDS, RESTRICTED_NAME);
	return built('shared', 'group', 'Shared', SHARED_WORDS, SHARED_NAME);
}
