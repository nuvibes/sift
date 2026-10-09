/* One settings sub-page at a time, and who owns which settings. */

import type { Snippet } from 'svelte';

class Drilldown {
	/** The sub-page's title, and the flag for whether one is open at all. */
	title = $state<string | null>(null);
	/** What it draws. Belongs to the component that opened it; see the note above. */
	body = $state<Snippet | null>(null);
	/** The words on the press that opened it ("Edit"): a crumb of every settings path under it. */
	door = $state<string | null>(null);

	/** Setting key -> the group that owns it, so a deep link can reach a row one level down. */
	#owners = new Map<string, () => void>();
	/** Section -> how to open the place a key on it that nothing else claims is drawn. */
	#sections = new Map<string, () => void>();

	open(title: string, body: Snippet, door?: string): void {
		this.title = title;
		this.body = body;
		this.door = door ?? null;
	}

	close(): void {
		this.title = null;
		this.body = null;
		this.door = null;
	}

	/** Claim a set of setting keys, and say how to open the page they are on. */
	own(keys: readonly string[], open: () => void): () => void {
		for (const key of keys) this.#owners.set(key, open);
		return () => {
			for (const key of keys) {
				if (this.#owners.get(key) === open) this.#owners.delete(key);
			}
		};
	}

	/** Claim every key on a SECTION that nothing claims by name, and say how to open the place
	 * they are drawn. */
	ownSection(section: string, open: () => void): () => void {
		this.#sections.set(section, open);
		return () => {
			if (this.#sections.get(section) === open) this.#sections.delete(section);
		};
	}

	/** Open whichever group owns this setting, or the section's own claim for a key on it that no
	 *  group owns. False when nothing does, which is the common case. */
	reveal(key: string, section?: string): boolean {
		const open =
			this.#owners.get(key) ?? (section === undefined ? undefined : this.#sections.get(section));
		if (!open) return false;
		open();
		return true;
	}
}

export const drilldown = new Drilldown();

/** The keys a pane's search declaration files on one of its sub-pages: the rows the page draws by
 * hand, which the registry knows nothing about. */
export function filedUnder(
	declared: readonly { key?: string; name?: string; page?: string }[],
	page: string
): string[] {
	return declared.flatMap((entry) => {
		if (entry.page !== page) return [];
		if (entry.key) return [entry.key];
		return entry.name ? [byName(entry.name)] : [];
	});
}

/** What a page claims for a thing it draws that has a name and no key: a heading over a group, a
 * card, a row drawn by hand. */
export function byName(name: string): string {
	return `name:${name.trim().toLowerCase()}`;
}
