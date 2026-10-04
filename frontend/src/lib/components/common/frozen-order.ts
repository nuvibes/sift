/* Hold an order still while someone is using it.
 *
 * A queue sorts itself live: jobs finish, priorities change, and rows move. That is correct right up
 * until a pointer is over one of them. Then the list re-sorts, the row under the cursor becomes a
 * different row, and the click that was meant for one job cancels another. The person did nothing
 * wrong and there is nothing on screen afterwards to explain what happened.
 *
 * So the order is a snapshot taken when a pointer or the keyboard arrives, and the live order comes
 * back when it leaves. This is the whole rule, in one place, because every list that shows live work
 * needs it and a list that reimplements it is a list that gets it subtly wrong.
 */

/**
 * Order `incoming` the way `frozenKeys` had it.
 *
 * Rows that have appeared since the snapshot go to the end, in the order they arrived: they are new,
 * nobody is reaching for them, and putting them anywhere else would move something. Rows that have
 * gone are simply absent: a finished job that vanished from the queue cannot be held on screen by
 * pretending it is still there.
 *
 * `frozenKeys` of `null` means nothing is held: the caller gets the live order untouched.
 */
export function applyFrozenOrder<T>(
	incoming: readonly T[],
	frozenKeys: readonly string[] | null,
	key: (item: T) => string
): T[] {
	if (frozenKeys === null) return [...incoming];

	const rank = new Map(frozenKeys.map((k, index) => [k, index]));

	// Sorted on a copy, by rank, with anything unranked after everything ranked. Array.sort is stable,
	// so new rows keep the order they came in and do not shuffle among themselves.
	return [...incoming].sort((a, b) => {
		const rankA = rank.get(key(a)) ?? Number.MAX_SAFE_INTEGER;
		const rankB = rank.get(key(b)) ?? Number.MAX_SAFE_INTEGER;
		return rankA - rankB;
	});
}
