// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * A named filter in an address, taken apart and put back together.
 *
 * ## Why this is a module and not three functions inside the bar
 *
 * One string carries three things at once: whether the filter is EXCLUDED (a leading minus), how
 * its values combine (a pipe for any, a comma for all), and the values themselves. Every reader
 * that takes it apart for itself gets a different part wrong: the minus stuck to the first value,
 * so `-Alice` is a different value from `Alice` and can be added twice, or a comma-joined list read
 * as one long value matching nothing anybody could click. So there is one pair of functions.
 *
 * A SAVED filter is the same string. Showing what one holds (as chips, in a tooltip, before
 * anybody presses it) means reading the same three things out of the same shape, and a second
 * reader would be the same class of drift one file further along.
 *
 * ## Why it sits in `$lib` rather than beside the bar
 *
 * `asNamedFilters` gives it a reader that is not a component: the store holding the kept searches,
 * which normalises what it loads and what it saves. Nothing else in `$lib` reaches into
 * `$lib/components`, and a store importing out of a component directory would be the first, on the
 * strength of one function. The module is the query language's, not the bar's.
 */

import { FIELDS, parseQuery, type ParsedClause } from '$lib/search/search.svelte';

/** One named filter: which values, how they combine, and whether they are being refused. */
export interface Named {
	excluded: boolean;
	all: boolean;
	values: string[];
}

/** A named filter with the dimension it belongs to. What a saved filter is a list of. */
export interface Part extends Named {
	field: string;
}

/** Take a parameter's value apart. See the head of this file for the three things it carries. */
export function taken(value: string): Named {
	const excluded = value.startsWith('-');
	const body = excluded ? value.slice(1) : value;
	// Split on both separators. Which one was used is remembered separately; a value list is a
	// value list either way, and reading only pipes would make an all-of column unclickable.
	const values = body
		.split(/[|,]/)
		.map((one) => one.trim())
		.filter(Boolean);
	return { excluded, all: !body.includes('|') && values.length > 1, values };
}

/** Write one back. The inverse of `taken`, and the only place that spells the separators. */
export function written(held: Named): string {
	return `${held.excluded ? '-' : ''}${held.values.join(held.all ? ',' : '|')}`;
}

/**
 * Every named filter in a query string, in the order it was written.
 *
 * WHICH NAMES ARE FILTERS is not the same question on every wall, which is why the vocabulary can
 * be handed in. On a wall of files it is the query language's whole field list: the default, and
 * what six screens are. On a wall of people it is that noun's own facets and nothing else: `sort`,
 * `prefix` and the anchor are in that address too, and they are how the wall is PAGED rather than
 * what it is filtered by. Reading the file language there would draw none of its filters and one of
 * its page controls, which is worse than drawing nothing. The bar works the same question out for
 * its own chips (see `filterNames` in `FilterBar`) and hands the answer down rather than
 * keeping a second copy of it.
 *
 * `q` is left out because it is FREE TEXT rather than a named filter: it has no dimension and no
 * values to tick, and the server is the only thing that can take it apart. Anything the query
 * language does not name is left out too: a saved filter carries the cursor it was saved at
 * (`from=...`), which is a position in a page of results rather than something anybody chose, and
 * drawing it would put an unreadable identifier at the front of every list.
 */
export function partsOf(query: string, names: readonly string[] = FIELDS): Part[] {
	const out: Part[] = [];
	for (const [field, value] of new URLSearchParams(query)) {
		if (field === 'q') continue;
		if (!names.includes(field)) continue;
		const held = taken(value);
		if (held.values.length > 0) out.push({ field, ...held });
	}
	return out;
}

/**
 * Whether two queries hold the SAME named filters, whatever order they were written in.
 *
 * For naming the screen: when what is filtering it is exactly a filter somebody kept, the bar says
 * so. Compared rather than remembered from the press, because a note saying "they clicked `save`"
 * goes on saying it after the next tick turns the screen into something that filter does not
 * describe, and a label that is wrong is worse than no label.
 *
 * Order-insensitive, because a browser serialises parameters in whatever order they were set and two
 * identical filters can be spelled either way round. The values inside a part keep THEIR order,
 * which is right: `a|b` and `b|a` are the same set of files and are not the same thing to read, and
 * a kept filter should be recognised as the thing it was saved as.
 */
export function sameFilters(one: Part[], other: Part[]): boolean {
	if (one.length !== other.length) return false;
	const spelled = (parts: Part[]) => parts.map((part) => `${part.field}=${written(part)}`).sort();
	const [here, there] = [spelled(one), spelled(other)];
	return here.every((each, at) => each === there[at]);
}

/**
 * One clause of a typed query as the named parameter that means the same thing, or null.
 *
 * The two spellings are one language: the server compiles `?tags=a|b` and `tags:a OR b` through the
 * same node builder, so a clause naming one field with values in it can be written either way and
 * mean exactly the same set of files. That is what makes the move below a change of SPELLING rather
 * than a rewrite of somebody's filter.
 *
 * Null for everything that cannot be said as a parameter, and each of those is a real case rather
 * than caution:
 *
 * - a clause across two dimensions has no field to be the parameter's name;
 * - a presence question (`loops:any`) has no values, and its words are read as values by the
 *   parameter side, so writing one would be inventing a spelling;
 * - a value carrying a quote, a comma or a pipe needs QUOTING, and the quoting rules are the
 *   grammar, the one thing this client refuses to hold a second copy of.
 *
 * Left alone, such a clause stays in `q` written the way the server wrote it, which is the same
 * thing `rowFor` does with a clause it cannot rebuild.
 */
function asParameter(clause: ParsedClause): [string, string] | null {
	const field = clause.field;
	if (field === null || field === undefined) return null;
	if (!(FIELDS as readonly string[]).includes(field)) return null;
	if (clause.present !== null && clause.present !== undefined) return null;
	if (clause.values.length === 0) return null;
	if (clause.values.some((one) => /["|,]/.test(one))) return null;
	return [
		field,
		written({ excluded: clause.negated, all: clause.match !== 'any', values: clause.values })
	];
}

/**
 * A stored query with every filter it names moved OUT of `q` and into a named parameter.
 *
 * ## The fault this exists for
 *
 * `q` is the search box's own text. A kept filter saved while something was typed carries that text
 * with it, so applying the filter would put it back in the box: seven chips in the search bar on
 * the top row, from a gesture that was not typing. And nothing could SHOW what such a filter holds:
 * `partsOf` reads named parameters, so a filter whose whole content is in `q` would draw one chip
 * for the one parameter beside it and say nothing about the other seven. Both are one fault.
 *
 * ## Why moving it is safe, and why it is not a rewrite
 *
 * The server parses `q` and the named parameters into the same nodes (`parse` joins
 * `parse_tokens` and `parse_modal`, and both reach `_listed`), so the two spellings are the same
 * query. This asks the server what the text means and writes each clause back the way the panel
 * writes one. It never takes the string apart itself; the clauses it cannot spell stay exactly as
 * the server wrote them.
 *
 * Free WORDS stay in `q`, because words are what the box is for. A filter kept from a search for
 * `beach party` still puts those words in the box when it is applied, and that is right.
 *
 * A parse that fails leaves everything where it was: `parseQuery` answers with the text and no
 * clauses, nothing is moved, and the filter goes on working exactly as it did.
 */
export async function asNamedFilters(query: string): Promise<string> {
	const params = new URLSearchParams(query);
	const typed = params.get('q') ?? '';
	if (!typed.trim()) return query;

	const parsed = await parseQuery(typed);
	const left: string[] = [];
	const moved: [string, string][] = [];
	for (const clause of parsed.clauses) {
		const named = asParameter(clause);
		if (named === null) left.push(clause.query);
		else moved.push(named);
	}
	if (moved.length === 0) return query;

	/* What is left of the box's text: the clauses that could not be moved, then the free words.
	   That is the order `q` is written in everywhere, because every offset the box holds is into that. */
	const rest = [...left, parsed.text].filter(Boolean).join(' ').trim();
	if (rest) params.set('q', rest);
	else params.delete('q');
	// Appended rather than set: a dimension can already carry a parameter, and two values of one
	// name are ANDed by the server exactly as two clauses of one field in `q` were.
	for (const [field, value] of moved) params.append(field, value);
	return params.toString();
}

/**
 * Every named filter in a query somebody TYPED, rather than in named parameters.
 *
 * `partsOf` above reads `tags=beach&type=video`, which is what the Filters screen writes. A cell of
 * a saved wall does not hold one of those: it holds the query in the language people type
 * (`sites:Discord in:"Sift Downloads" media:video`), and without this the bubble describing a
 * saved wall would draw that string out as words while the bubble describing a saved FILTER, one
 * screen over, draws the same kind of thing as chips.
 *
 * This is the inverse of `savedQuery`, which is the function that WRITES the spelling: a token per
 * filter, `field:value`, the value quoted when it holds a space, a comma or a quote. Anything else
 * in the string (free words, a token naming a field this version does not know) is left out
 * rather than guessed at, and the caller shows its own words when nothing comes back.
 *
 * Kept beside the reader it mirrors rather than in the search module, because the two answer one
 * question in two spellings and a reader that drifts from its writer is the fault this file exists
 * to prevent.
 */
export function typedParts(typed: string, fields: readonly string[]): Part[] {
	const out: Part[] = [];
	// One pass, quote-aware: a value in quotes may hold the space that would otherwise end a token.
	const tokens = typed.match(/(?:[^\s"]+|"[^"]*")+/g) ?? [];
	for (const token of tokens) {
		const at = token.indexOf(':');
		if (at <= 0) continue;
		const excluded = token.startsWith('-');
		const field = token.slice(excluded ? 1 : 0, at);
		if (!fields.includes(field)) continue;
		const value = token.slice(at + 1).replace(/^"|"$/g, '');
		if (!value.trim()) continue;
		const held = taken(excluded ? `-${value}` : value);
		if (held.values.length === 0) continue;
		// One chip per field, the way a named query gives one parameter per field.
		const already = out.find((one) => one.field === field && one.excluded === held.excluded);
		if (already) already.values.push(...held.values);
		else out.push({ field, ...held });
	}
	return out;
}
