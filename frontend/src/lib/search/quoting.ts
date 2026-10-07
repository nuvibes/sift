// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * How one filter's values are written into a query and read back out of one: the quoting rule the
 * server's parser holds, and the only copy of it in the client.
 */

/* A value written so the parser reads it back as the same value.
 *
 * Quoted when it contains whitespace, a comma or a pipe, which would otherwise end the value, or
 * begins with a minus, which would otherwise refuse it. The quotes inside it are DROPPED rather
 * than escaped, and that is a real limitation rather than a choice: the query language has no
 * escape for a quote, so a tag genuinely named `say "hi"` cannot be written as a token at all.
 * Dropping them produces a query for a name that does not exist, which finds nothing: the narrow
 * direction, and the only safe way to be wrong here. Escaping in the language would fix it.
 */
export function quoted(value: string): string {
	return /^-|[\s,"|]/.test(value) ? `"${value.replaceAll('"', '')}"` : value;
}

/** A value as a named parameter holds it: quoted where a separator or a minus would misread it. */
export function parameterValue(value: string): string {
	return /^-|[|,]/.test(value) ? quoted(value) : value;
}

/**
 * The values in one filter's value text, and whether a pipe joined them (any) rather than a comma
 * (all). Quote-aware as the server's splitter is: inside quotes a pipe, a comma or a leading minus
 * belongs to the value, and the quotes themselves are dropped.
 */
export function valuesIn(body: string): { values: string[]; any: boolean } {
	const values: string[] = [];
	let current = '';
	let inside = false;
	let any = false;
	for (const character of body) {
		if (character === '"') inside = !inside;
		else if (!inside && (character === '|' || character === ',')) {
			any ||= character === '|';
			values.push(current);
			current = '';
		} else current += character;
	}
	values.push(current);
	return { values: values.map((one) => one.trim()).filter(Boolean), any };
}
