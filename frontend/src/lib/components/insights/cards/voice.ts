/*
 * The key words of a card's context, set in the heavier weight as a recap's lines mark them: each
 * number with the unit or the time of day it carries ("40 files", "9 PM", "1,240").
 */

/** A number, then the word it counts or the half of the day it is in, if one follows. */
const KEY =
	/\d+(?:[,.:]\d+)*(?:\s(?:AM|PM|h|min|hours?|minutes?|files?|views?|times?|days?|weeks?|people|Sites?))?/g;

/** A run of plain text cut into its key words and the words between them, in order. */
export function keyWords(text: string): { text: string; key: boolean }[] {
	const parts: { text: string; key: boolean }[] = [];
	let from = 0;
	for (const found of text.matchAll(KEY)) {
		if (found.index > from) parts.push({ text: text.slice(from, found.index), key: false });
		parts.push({ text: found[0], key: true });
		from = found.index + found[0].length;
	}
	if (from < text.length) parts.push({ text: text.slice(from), key: false });
	return parts;
}
