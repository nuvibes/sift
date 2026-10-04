// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Telling apart two folders that are called the same thing.
 *
 * A media library is full of repeated names (every person's folder holds an "Images" and a
 * "Videos"), so a chooser in the Add panel would list nine rows reading "Images" and nothing else.
 * Nine identical rows is not a list somebody can pick from; it is nine guesses.
 *
 * The obvious repair is to put the whole path in the label. It is the wrong shape twice over: the path is mostly the names of
 * parents that are the same for every row in the list, so the part that TELLS THEM APART is buried
 * in the middle of a long string, and a unique name ("Sift Downloads") ends up wearing a path
 * it never needed. What a reader wants is the name, and beside it the least amount of path that
 * makes this one that name rather than another.
 *
 * So: the name stays the label, and what is added is the SHORTEST suffix of the parent path that no
 * other option sharing that name can claim. Nine "Images" become "Images" beside "Juniper/2025",
 * "Images" beside "Kestrel" and so on, each carrying only as much as it needs, and a name nobody
 * else has is left bare.
 *
 * Pure, and here rather than in each chooser, because there are four of them (the Add panel, the
 * Move sheet, the default download folder and the naming rule's destination), and four copies of a
 * rule like this is four lists that disambiguate differently.
 */

/** An option that may know where it is on disk. `path` ends with the folder's own name. */
export interface Placed {
	value: string;
	label: string;
	/** The folder's path, as the caller spells it. Absent for an option that is not a folder. */
	path?: string;
	disabled?: boolean;
}

/** An option as `Select` takes it, with the quiet second phrase filled in where one is needed. */
interface Distinguished {
	value: string;
	label: string;
	detail?: string;
	disabled?: boolean;
}

/** The parents of a path: its segments with the folder's own name taken off the end. */
function parentsOf(one: Placed): string[] {
	const parts = (one.path ?? '')
		.split('/')
		.map((part) => part.trim())
		.filter(Boolean);
	// The last segment is the folder itself wherever the caller spelled the path that way, and
	// repeating the name inside its own detail says nothing. Where the path does NOT end with the
	// name (a caller spelling a path some other way), every segment is a parent and is kept.
	if (parts.length > 0 && parts[parts.length - 1] === one.label) parts.pop();
	return parts;
}

/**
 * The same options, with a `detail` on any whose label another option also has.
 *
 * Order, values and labels are untouched: this only ever ADDS the phrase that says which one this
 * is. An option with no path to draw on keeps no detail even when its name repeats: there is
 * nothing honest to say about it, and an empty phrase is worse than none.
 */
export function disambiguate(options: readonly Placed[]): Distinguished[] {
	const sharing = new Map<string, Placed[]>();
	for (const one of options) {
		const alike = sharing.get(one.label);
		if (alike) alike.push(one);
		else sharing.set(one.label, [one]);
	}

	return options.map((one) => {
		const bare: Distinguished = { value: one.value, label: one.label };
		if (one.disabled !== undefined) bare.disabled = one.disabled;

		const alike = sharing.get(one.label) ?? [];
		if (alike.length < 2) return bare;

		const mine = parentsOf(one);
		if (mine.length === 0) return bare;
		const others = alike.filter((other) => other !== one).map(parentsOf);

		for (let depth = 1; depth <= mine.length; depth += 1) {
			const tail = mine.slice(-depth).join('/');
			const claimed = others.some((other) => other.slice(-depth).join('/') === tail);
			if (!claimed) return { ...bare, detail: tail };
		}
		// Two folders of the same name in the same place: nothing tells them apart, so the whole
		// path is said rather than a suffix that would look meaningful and not be.
		return { ...bare, detail: mine.join('/') };
	});
}

/**
 * The order a folder chooser lists in: what this account used last, then everything else by name.
 *
 * ## Why the order is not the server's
 *
 * `/library/folders` answers in the order the library walks, which is neither alphabetical nor
 * anything a person could predict, so a chooser drawing it straight would ask somebody to read a
 * hundred rows to find one. Alphabetical is predictable, and the rest of the list is in it.
 *
 * What sits ABOVE it is the shortcut: a library has one folder per person, and the same handful is
 * where everything went this week. Those are put in front, in the order they were last used, up to
 * `RECENT_FOLDERS_KEPT`. See `$lib/shell/interface-state`, which is where the record lives and where
 * the reasoning for it being the account's rather than the browser's is written out.
 *
 * ## Why it is here rather than in each chooser
 *
 * The same reason `disambiguate` above is: there are four folder choosers and a rule like this
 * written out four times is four lists that order differently. Pure, so a caller hands in its
 * options and its recents and gets the order back: nothing here reads a store.
 *
 * The first row of a chooser (the default, "Sift decides") is NOT passed through this. It is
 * not a folder among the folders; it is the answer that applies when none is chosen, and it stays
 * where it is put.
 */
export function recentFirst<Option extends { value: string; label: string }>(
	options: readonly Option[],
	recent: readonly string[],
	kept: number
): Option[] {
	const byId = new Map(options.map((one) => [one.value, one]));
	const first: Option[] = [];
	for (const id of recent) {
		const found = byId.get(id);
		// Silently skipped where it names a folder this list does not hold. A folder can be removed
		// from the library, or handed back read-only, long after somebody downloaded into it, and
		// the record is deliberately never checked against anything, so this is the ordinary case
		// rather than a fault.
		if (!found || first.includes(found)) continue;
		first.push(found);
		if (first.length >= kept) break;
	}
	const already = new Set(first);
	const rest = options
		.filter((one) => !already.has(one))
		// `localeCompare` with the accent-insensitive strength every other list in this application
		// sorts by, so "Ema" and "Ema" with an accent on the E do not end up at opposite ends of one chooser.
		.sort((one, other) => one.label.localeCompare(other.label, undefined, { sensitivity: 'base' }));
	return [...first, ...rest];
}
