/* Which part of a suggestion was typed, as pieces rather than markup: a name may contain `<b>`. */

export interface Part {
	text: string;
	hit: boolean;
}

/**
 * Every occurrence, case-insensitively. The empty-needle guard is load-bearing: `indexOf('')`
 * answers 0 everywhere, and the loop would never end.
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
		/* Sliced from the ORIGINAL, so a name keeps its own capitals. */
		parts.push({ text: text.slice(at, at + looking.length), hit: true });
		from = at + looking.length;
	}

	if (from === 0) return [{ text, hit: false }];
	if (from < text.length) parts.push({ text: text.slice(from), hit: false });
	return parts;
}
