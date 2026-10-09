// SPDX-License-Identifier: AGPL-3.0-or-later
/* Telling apart two folders that are called the same thing. */

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
	// repeating the name inside its own detail says nothing.
	if (parts.length > 0 && parts[parts.length - 1] === one.label) parts.pop();
	return parts;
}

/** The same options, with a `detail` on any whose label another option also has. */
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

/** The order a folder chooser lists in: what this account used last, then everything else by
 * name. */
export function recentFirst<Option extends { value: string; label: string }>(
	options: readonly Option[],
	recent: readonly string[],
	kept: number
): Option[] {
	const byId = new Map(options.map((one) => [one.value, one]));
	const first: Option[] = [];
	for (const id of recent) {
		const found = byId.get(id);
		// Silently skipped where it names a folder this list does not hold.
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
