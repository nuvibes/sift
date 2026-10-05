/* Digits and symbols read as the letters they stand in for: `h0ll0wgrain` is Hollowgrain. */
const LOOKALIKE: Record<string, string> = {
	'0': 'o',
	'1': 'i',
	'3': 'e',
	'4': 'a',
	'5': 's',
	'7': 't',
	'8': 'b',
	'9': 'g',
	'@': 'a',
	$: 's',
	'!': 'i'
};

/* The likeness under which a name is not offered: a third of the runs, the usual trigram floor. */
export const NEAR_ENOUGH = 0.3;

function flatten(text: string): string {
	return [...text.toLowerCase().trim()]
		.map((char) => LOOKALIKE[char] ?? char)
		.filter((char) => /[a-z0-9]/.test(char))
		.join('');
}

function trigrams(text: string): Set<string> {
	const padded = ` ${flatten(text)} `;
	const held = new Set<string>();
	for (let at = 0; at + 3 <= padded.length; at += 1) held.add(padded.slice(at, at + 3));
	return held;
}

/** How alike two names are, 0 to 1: the runs of three they share over all the runs of both. */
export function likeness(name: string, typed: string): number {
	if (flatten(name).includes(flatten(typed))) return 1;
	const mine = trigrams(name);
	const theirs = trigrams(typed);
	let shared = 0;
	for (const run of theirs) if (mine.has(run)) shared += 1;
	const either = mine.size + theirs.size - shared;
	return either === 0 ? 0 : shared / either;
}

/** The nearest few of `people` to what was typed, nearest first, and none under the floor. */
export function nearNames<T extends { name: string }>(people: readonly T[], typed: string): T[] {
	if (!flatten(typed)) return [];
	return people
		.map((person) => ({ person, near: likeness(person.name, typed) }))
		.filter((one) => one.near >= NEAR_ENOUGH)
		.sort((one, other) => other.near - one.near)
		.slice(0, 8)
		.map((one) => one.person);
}
