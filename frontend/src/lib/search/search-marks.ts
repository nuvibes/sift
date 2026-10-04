/*
 * Which part of a suggestion is the part that was typed.
 *
 * The dropdown matches ANYWHERE in a name rather than only at the start of one (`ell` offers
 * Tobias Ellery, `gifs` offers "gifs archive"), which is what makes it useful and also what
 * makes it hard to read: a list of a dozen names with no telling why any of them is on it. Marking
 * the matched letters answers that at a glance, and it answers the more useful question underneath
 * it, which is whether the thing you are looking for is on the list yet.
 *
 * Split into pieces here rather than by building a string of markup, because a suggestion is a NAME
 * out of somebody's library: a person called `<b>` is not a hypothetical, and pasting text into
 * markup is how it stops being one. The caller renders the pieces as elements, so nothing in a name
 * is ever read as anything but text.
 */

/** One run of a suggestion: the letters, and whether they are what was typed. */
export interface Part {
	text: string;
	hit: boolean;
}

/**
 * Break `text` into marked and unmarked runs, on every occurrence of `needle`.
 *
 * Case-insensitively, because the box does not care and neither does the search behind it: somebody
 * typing `reya` and being shown `Reya Solberg` with nothing marked would read as the list having
 * offered a row for no reason.
 *
 * EVERY occurrence, not the first. `or` in "Orla Fennimore" is in both words, and marking one of
 * them makes the other look like the reason the row is not a match.
 *
 * An empty needle marks nothing, which is the whole answer for a box nobody has typed in yet: the
 * dropdown lists the filters that exist, and marking all of every one of them is a list in bold.
 * It is also the one guard here that is load-bearing rather than tidy: `indexOf('')` answers 0 at
 * every offset, so the loop below never advances and never ends. Taking the guard out does not
 * mis-mark a row, it hangs the tab.
 */
export function marks(text: string, needle: string): Part[] {
	const looking = needle.trim().toLowerCase();
	if (!looking || !text) return [{ text, hit: false }];

	const parts: Part[] = [];
	const hay = text.toLowerCase();
	let from = 0;

	for (;;) {
		const at = hay.indexOf(looking, from);
		if (at === -1) break;
		if (at > from) parts.push({ text: text.slice(from, at), hit: false });
		/* Sliced out of the ORIGINAL rather than out of the lowercased copy, so a name keeps its own
		   capitals: `reya` must mark the `Reya` that is there, not replace it with what was typed. */
		parts.push({ text: text.slice(at, at + looking.length), hit: true });
		from = at + looking.length;
	}

	if (from === 0) return [{ text, hit: false }];
	if (from < text.length) parts.push({ text: text.slice(from), hit: false });
	return parts;
}
