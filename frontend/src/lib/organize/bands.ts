/* What each band of the board is called, and the order they are drawn in.
 *
 * The SERVER decides which band a queue is in. See `kernel/workbench.Band`, where the whole
 * argument lives. This is the client's half of the same arrangement and it is deliberately only
 * two things: the heading over each band, and the order. Nothing here knows what any queue holds,
 * and adding a queue to a band is a one-word change on the server with no edit in this file.
 *
 * ## Why a band a client does not know is not an error
 *
 * A newer server can send a band this build has never heard of. That is the same situation as a
 * queue this build cannot draw, and the answer is the same one the panel registry already gives:
 * fall back rather than throw. An unknown band lands at the end under the last heading, which is
 * honest (the card still says what it is and how much of it there is), and nothing disappears.
 * Making it a closed union here would turn a NEW band into a broken screen on every older client,
 * which is the exact failure the registry exists to prevent.
 */

/** A band, as the server names it. Not a closed set. See above.
 *
 * Not exported: `BANDS` and `bandsOf` are what anybody outside this file reaches for, and both
 * carry this type structurally. A name on the way out of a module is a claim that somebody else
 * may depend on it. */
type BandName = string;

/** The heading over one band, and what that band is for. Not exported, for the reason above. */
interface BandHeading {
	name: BandName;
	/** The word over the band. Absent for the first one. See below. */
	title?: string;
	/** What this band is, for anybody who cannot see the grouping. Always present. */
	label: string;
}

/*
 * The bands, in the order the board draws them.
 *
 * The FIRST band carries no visible heading, and that is not an oversight. It is the work, it is
 * the top of the screen, and the page is already titled "Organize" one line above it: a heading
 * reading "Needs you" under a heading reading "Organize" is furniture between somebody and the
 * thing they came for. The bands below it need their headings precisely because they are NOT the
 * work, and the heading is what says so.
 *
 * It still carries a `label`, which is what a screen reader announces for the region. The visual
 * heading and the accessible name are different jobs and only one of them is redundant here.
 */
export const BANDS: readonly BandHeading[] = [
	{ name: 'decision', label: 'Waiting for you' },
	{
		name: 'cleanup',
		title: 'Tidying up',
		label: 'Tidying up'
	},
	{
		name: 'log',
		title: "What Sift couldn't read",
		label: "What Sift couldn't read"
	}
] as const;

/**
 * The band a queue belongs in, or the last one when this build has never heard of it.
 *
 * Records are not here at all: a `record` band queue is drawn by its GROUP's tabs rather than as a
 * card, because a count that never goes down cannot sit on a screen whose promise is that it
 * empties. `bandsOf` leaves them out for that reason and `tabsFor` is what finds them again.
 */
export function bandsOf<
	T extends { name: string; band: string; pending: boolean; group: string | null; count: number }
>(queues: readonly T[]): { heading: BandHeading; cards: Card<T>[] }[] {
	const known = new Set(BANDS.map((one) => one.name));
	const last = BANDS[BANDS.length - 1];
	const held = new Map<string, Card<T>[]>(BANDS.map((one) => [one.name, []]));
	const byGroup = new Map<string, Card<T>>();
	for (const queue of queues) {
		// A record is never a card of its own; it rides along on its group's card below. `pending`
		// is the server's own derivation of that: one answer, read here rather than re-derived
		// from the band.
		if (!queue.pending) continue;
		const joined = queue.group ? byGroup.get(queue.group) : undefined;
		if (joined) {
			/* A second card for a group already on the board JOINS it rather than sitting beside
			 * it. The band is the first queue's: a group is one page, so it has one place on
			 * this screen, and the first queue is what says which. Near duplicates is a judgement
			 * and exact copies are housekeeping, and drawn apart they would be two ways in to one
			 * screen, in two bands.
			 */
			joined.queues.push(queue);
			joined.count += queue.count;
			continue;
		}
		const where = known.has(queue.band) ? queue.band : last.name;
		const card: Card<T> = { lead: queue, queues: [queue], count: queue.count };
		held.get(where)?.push(card);
		if (queue.group) byGroup.set(queue.group, card);
	}
	/*
	 * ...AND THEN THE RECORDS OF EACH GROUP, on the card their group already has.
	 *
	 * A card is the page it opens, and that page's tabs include its records. Dropping them would
	 * leave the Folders card a card of one queue, named after its one tab ("Folders to review")
	 * rather than after the page it opens ("Folders"). See `titleOf`.
	 *
	 * NOT COUNTED, which is the whole of the care needed here. `count` is what the card leads with
	 * and it is work waiting; a record's count never goes down, so adding it would make the number
	 * on the card unable to reach zero: exactly what keeping records off the board is for.
	 *
	 * A record whose group has no card is still dropped, which is right: the work it is a record of
	 * is empty or absent, and a card that is only a record is a card that can never be finished.
	 */
	for (const queue of queues) {
		if (queue.pending || !queue.group) continue;
		byGroup.get(queue.group)?.queues.push(queue);
	}
	return BANDS.map((heading) => ({ heading, cards: held.get(heading.name) ?? [] })).filter(
		(band) => band.cards.length > 0
	);
}

/**
 * One card on the board, which is one PAGE rather than one queue.
 *
 * A queue standing alone is a card of one. A group of two or more is one card: they are tabs of
 * one screen, so two cards pointing at two of those tabs would be two ways in to one job, and for
 * duplicates would put them in two different bands, the same screen listed twice on one board.
 *
 * A group whose LEAD declares no name for it is left as separate cards, which is the honest
 * fallback: the card would otherwise have to be titled after one of its halves, and *Near
 * Duplicates* over a card that opens exact copies as well is a card lying about half of what it
 * does.
 */
export interface Card<T> {
	/** The queue that names the card, carries its icon and sentence, and is opened by it. */
	lead: T;
	/**
	 * Every queue on it, the first queue first, with the group's records last. One long for a queue
	 * that stands alone.
	 */
	queues: T[];
	/** What is waiting across the ones that are WORK. One decision each, whatever kind of decision
	 *  it is: a record contributes nothing, because its count never goes down. */
	count: number;
}

/** What a card is called: the group's own name, or the queue's where it stands alone. */
export function titleOf<T extends { title: string; group_title?: string | null }>(card: {
	lead: T;
	queues: T[];
}): string {
	if (card.queues.length < 2) return card.lead.title;
	return card.lead.group_title ?? card.lead.title;
}

/**
 * The queues that share a page with this one, in the order they registered.
 *
 * A group of one is not a group and answers nothing: a row of tabs with a single word in it is a
 * heading that looks like a control. The server says so by leaving `group` empty, and this says so
 * again for the case where a group's other members are absent on this install: a feature switched
 * off takes its queue off the board entirely, and the tab row must not be left as a lone word.
 */
export function tabsFor<T extends { name: string; group: string | null }>(
	queues: readonly T[],
	here: string
): T[] {
	const mine = queues.find((one) => one.name === here)?.group;
	if (!mine) return [];
	const shared = queues.filter((one) => one.group === mine);
	return shared.length > 1 ? shared : [];
}

/**
 * The trail for a screen under Organize: the board, the page, the tab, then this screen.
 *
 * ## The page is a crumb of its own
 *
 * A queue that is one tab of a page names its page first ("Organize > Faces > Needs Your Input"),
 * so that five tabs of one screen do not look like five unrelated places, with a detail opened
 * from one of them reading as a sixth. The page's name is the first queue's `group_title`, the same
 * word the board's card wears. A queue that stands alone is a page in its own right and gets no
 * extra crumb.
 *
 * And a DETAIL names the tab it was opened from rather than the one it is registered under: `from`
 * carries that, and `tabOpenedFrom` refuses a name that is not on the same page.
 *
 * Organize, then the queue, then here, and the queue crumb is dropped when this IS the queue,
 * because the trail would otherwise say the queue's name twice in a row. A DETAIL screen names the
 * queue it is a detail OF, so the trail leads back through the pile it came from rather than
 * jumping to the board. A fixed screen that is not a queue (the decision record) passes no queue
 * and gets Organize, then its own name.
 *
 * The queue's title comes from the board, which a detail screen may not have fetched yet. And
 * until it has, that crumb IS NOT DRAWN. "Organize" standing in until the board landed would put
 * "Organize > Organize > A group of faces" on the screen on a direct load of a pile page. That is
 * not a placeholder, it is a wrong answer: the middle crumb links to `/organize/<queue>` while
 * saying the name of the board above it, so the trail says one word twice and one of the two is a
 * lie about where it goes.
 *
 * Waiting is the cheaper mistake of the two available. A crumb that arrives is a step appearing at
 * the end of a short trail; a crumb that is WRONG is read, acted on, and only then replaced. It is
 * the same fallback either way (the label is the only part that is unknown, never the address),
 * so nothing is lost by leaving it out until it can be named.
 *
 * Here, beside the tabs, rather than in the header: the trail is drawn by the FRAME and every
 * Organize screen hands it there, so the rule that shapes it has to be one function each of them
 * can call.
 */
export function organizeCrumbs<
	T extends { name: string; title: string; group?: string | null; group_title?: string | null }
>(
	queues: readonly T[],
	queue: string | undefined,
	here: string | undefined,
	title?: string,
	from?: string | null
): { label: string; href?: string }[] {
	const board = { label: 'Organize', href: '/organize' };
	// WHICH TAB THIS SCREEN BELONGS TO. A detail says where it was opened FROM; anything else is
	// the queue itself. See `tabOpenedFrom` for why a stranger's name is refused.
	const tab = (here === undefined ? undefined : tabOpenedFrom(queues, queue, from)) ?? queue;
	// What the queue is CALLED, or nothing at all. The board is what knows, and a screen opened
	// directly has not heard from it yet. See above for why nothing is the right answer then.
	const named = queues.find((one) => one.name === tab)?.title ?? title;
	const page = groupCrumb(queues, tab);
	if (here === undefined) {
		return named === undefined ? [board] : [board, ...page, { label: named }];
	}
	if (tab === undefined || named === undefined) return [board, { label: here }];
	return [board, ...page, { label: named, href: `/organize/${tab}` }, { label: here }];
}

/**
 * The page a queue is a tab OF, as a crumb, or nothing where it is not on one.
 *
 * Jumping from the board straight to the tab ("Organize > Ignored", and a detail opened from it
 * "Organize > Unnamed faces > A group of faces") would make five tabs of one page five
 * different-looking places, none of them saying what page they were on. One crumb for the page and
 * one for the tab says both, in the order somebody walked.
 *
 * The group's NAME is the first queue's `group_title`, which is the same field the board's card is
 * titled from: one answer to "what is this page called", read here rather than spelled a second
 * time. A group whose first queue declares none is not a page with a name, so it gets no crumb: a
 * middle crumb titled after half of itself is worse than none at all, which is the rule `titleOf`
 * already makes.
 */
function groupCrumb<
	T extends { name: string; title: string; group?: string | null; group_title?: string | null }
>(queues: readonly T[], queue: string | undefined): { label: string; href?: string }[] {
	const group = queues.find((one) => one.name === queue)?.group;
	if (!group) return [];
	const lead = queues.find((one) => one.group === group && one.group_title);
	if (!lead) return [];
	return [{ label: lead.group_title as string, href: `/organize/${lead.name}` }];
}

/**
 * The tab a detail screen was opened from, when the address names one it may have been.
 *
 * **The origin travels with the detail rather than being guessed from the queue it belongs to.** A
 * group opened from Ignored and a group opened from Unnamed faces are one screen, so the screen
 * cannot know which tab somebody left, and a guess would put the way back on the tab the detail
 * is registered under, so a group pressed on Ignored could not get back to Ignored at all.
 *
 * Only a name on the SAME PAGE is honoured. An address is somebody else's to write, and a `from`
 * naming any queue at all would let a link draw a trail through a screen this detail has nothing to
 * do with: a crumb that is read, followed, and only then found to be a lie. A stranger's name is
 * dropped and the detail's own queue answers.
 */
export function tabOpenedFrom<T extends { name: string; group?: string | null }>(
	queues: readonly T[],
	queue: string | undefined,
	from: string | null | undefined
): string | undefined {
	if (!from || from === queue) return undefined;
	const group = queues.find((one) => one.name === queue)?.group;
	const asked = queues.find((one) => one.name === from);
	return group && asked?.group === group ? from : undefined;
}
