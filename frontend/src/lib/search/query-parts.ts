// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * A named filter in an address (minus, separator, values), taken apart and put back in one place.
 */

import { parameterValue, valuesIn } from '$lib/search/quoting';
import { FIELDS, parseQuery, type ParsedClause } from '$lib/search/search.svelte';

export interface Named {
	excluded: boolean;
	all: boolean;
	values: string[];
}

export interface Part extends Named {
	field: string;
}

export function taken(value: string): Named {
	// Only a minus outside the quotes refuses: `"-a"` is a value that begins with one.
	const excluded = value.startsWith('-') && value.length > 1;
	// Split on both separators; which one was used is remembered separately.
	const { values, any } = valuesIn(excluded ? value.slice(1) : value);
	return { excluded, all: !any && values.length > 1, values };
}

/** The inverse of `taken`, and the only place that spells the separators. */
export function written(held: Named): string {
	const values = held.values.map(parameterValue);
	return `${held.excluded ? '-' : ''}${values.join(held.all ? ',' : '|')}`;
}

/** Every named filter in a query string; the vocabulary depends on the wall. `q` is left out. */
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
 * Whether two queries hold the SAME named filters in any order: a browser orders parameters as it
 * likes. The values inside a part keep their order.
 */
export function sameFilters(one: Part[], other: Part[]): boolean {
	if (one.length !== other.length) return false;
	const spelled = (parts: Part[]) => parts.map((part) => `${part.field}=${written(part)}`).sort();
	const [here, there] = [spelled(one), spelled(other)];
	return here.every((each, at) => each === there[at]);
}

/**
 * One clause of a typed query as the named parameter meaning the same, or null where it cannot be
 * said as one: two dimensions, a presence question, or a value that would need quoting.
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
 * A stored query with every filter it names moved OUT of `q` into named parameters, so applying it
 * does not fill the search box and its chips can be drawn. The server parses the text; a clause it
 * cannot spell, free words and a failed parse all stay where they were.
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

	/* The order `q` is written in everywhere: unmoved clauses, then the words. */
	const rest = [...left, parsed.text].filter(Boolean).join(' ').trim();
	if (rest) params.set('q', rest);
	else params.delete('q');
	// Appended: two values of one name are ANDed, as two clauses were.
	for (const [field, value] of moved) params.append(field, value);
	return params.toString();
}

/** Every named filter in a TYPED query, the inverse of `savedQuery`; anything else is left out. */
export function typedParts(typed: string, fields: readonly string[]): Part[] {
	const out: Part[] = [];
	const tokens = typed.match(/(?:[^\s"]+|"[^"]*")+/g) ?? [];
	for (const token of tokens) {
		const at = token.indexOf(':');
		if (at <= 0) continue;
		const excluded = token.startsWith('-');
		const field = token.slice(excluded ? 1 : 0, at);
		if (!fields.includes(field)) continue;
		const value = token.slice(at + 1);
		const signed = value.startsWith('-') && value.length > 1;
		const { values, any } = valuesIn(signed ? value.slice(1) : value);
		if (values.length === 0) continue;
		const held: Named = { excluded: excluded || signed, all: !any && values.length > 1, values };
		const already = out.find((one) => one.field === field && one.excluded === held.excluded);
		if (already) already.values.push(...held.values);
		else out.push({ field, ...held });
	}
	return out;
}
