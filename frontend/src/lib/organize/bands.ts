/* What each band of the board is called, and the order they are drawn in. */

/** A band, as the server names it. Not a closed set. */
type BandName = string;

/** The heading over one band, and what that band is for. Not exported, for the reason above. */
interface BandHeading {
	name: BandName;
	/** The word over the band. Absent for the first one. See below. */
	title?: string;
	/** What this band is, for anybody who cannot see the grouping. Always present. */
	label: string;
}

/* The bands, in the order the board draws them. */
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

/** The band a queue belongs in, or the last one when this build has never heard of it. */
export function bandsOf<
	T extends { name: string; band: string; pending: boolean; group: string | null; count: number }
>(queues: readonly T[]): { heading: BandHeading; cards: Card<T>[] }[] {
	const known = new Set(BANDS.map((one) => one.name));
	const last = BANDS[BANDS.length - 1];
	const held = new Map<string, Card<T>[]>(BANDS.map((one) => [one.name, []]));
	const byGroup = new Map<string, Card<T>>();
	for (const queue of queues) {
		// A record is never a card of its own; it rides along on its group's card below.
		if (!queue.pending) continue;
		const joined = queue.group ? byGroup.get(queue.group) : undefined;
		if (joined) {
			/* A second card for a group already on the board JOINS it rather than sitting beside
			 * it. */
			joined.queues.push(queue);
			joined.count += queue.count;
			continue;
		}
		const where = known.has(queue.band) ? queue.band : last.name;
		const card: Card<T> = { lead: queue, queues: [queue], count: queue.count };
		held.get(where)?.push(card);
		if (queue.group) byGroup.set(queue.group, card);
	}
	/* ...AND THEN THE RECORDS OF EACH GROUP, on the card their group already has. */
	for (const queue of queues) {
		if (queue.pending || !queue.group) continue;
		byGroup.get(queue.group)?.queues.push(queue);
	}
	return BANDS.map((heading) => ({ heading, cards: held.get(heading.name) ?? [] })).filter(
		(band) => band.cards.length > 0
	);
}

/** One card on the board, which is one PAGE rather than one queue. */
export interface Card<T> {
	/** The queue that names the card, carries its icon and sentence, and is opened by it. */
	lead: T;
	/** Every queue on it, the first queue first, with the group's records last. */
	queues: T[];
	/** What is waiting across the ones that are WORK. */
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

/** The queues that share a page with this one, in the order they registered. */
export function tabsFor<T extends { name: string; group: string | null }>(
	queues: readonly T[],
	here: string
): T[] {
	const mine = queues.find((one) => one.name === here)?.group;
	if (!mine) return [];
	const shared = queues.filter((one) => one.group === mine);
	return shared.length > 1 ? shared : [];
}

/** The trail for a screen under Organize: the board, the page, the tab, then this screen. */
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
	// the queue itself.
	const tab = (here === undefined ? undefined : tabOpenedFrom(queues, queue, from)) ?? queue;
	// What the queue is CALLED, or nothing at all. The board is what knows, and a screen opened
	// directly has not heard from it yet.
	const named = queues.find((one) => one.name === tab)?.title ?? title;
	const page = groupCrumb(queues, tab);
	if (here === undefined) {
		return named === undefined ? [board] : [board, ...page, { label: named }];
	}
	if (tab === undefined || named === undefined) return [board, { label: here }];
	return [board, ...page, { label: named, href: `/organize/${tab}` }, { label: here }];
}

/** The page a queue is a tab OF, as a crumb, or nothing where it is not on one. */
function groupCrumb<
	T extends { name: string; title: string; group?: string | null; group_title?: string | null }
>(queues: readonly T[], queue: string | undefined): { label: string; href?: string }[] {
	const group = queues.find((one) => one.name === queue)?.group;
	if (!group) return [];
	const lead = queues.find((one) => one.group === group && one.group_title);
	if (!lead) return [];
	return [{ label: lead.group_title as string, href: `/organize/${lead.name}` }];
}

/** The tab a detail screen was opened from, when the address names one it may have been. */
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
