/* What the badge in the corner of a thing means, and the words for it. */

import type { IconName } from '$lib/design/icons';
import type { components } from '$lib/api/schema';

/** What a mark's tooltip ends with, because a mark that can be pressed says so. */
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

/* Hidden, split the way the other three are. */
export const HIDDEN_VERB = 'Hidden';
export const HIDDEN_REST = '\u2014 visible to you only when you unhide with your PIN';
export const HIDDEN_WORDS = `${HIDDEN_VERB} ${HIDDEN_REST}`;

/** The same fact as a sentence that stands on its own. */
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

/** The mark for one thing, or null when nothing has been said about it, which is most of a
 * library. */
export function markFor(facts: MarkFacts, { file = false } = {}): Mark | null {
	const shared = facts.shared === true;
	const restricted = facts.restricted === true;
	if (!shared && !restricted) return null;

	// WHERE THE SWITCH IS, read from the facts wherever they say.
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
