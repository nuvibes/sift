/*
 * One settings sub-page at a time, and who owns which settings.
 *
 * ## What a drill-in is
 *
 * A group of settings that answer one question gets ONE control on the pane ("For every field:
 * Fill in what is missing"), and an Edit that replaces the pane with a page holding all of them,
 * with a way back. It is the shape a game's graphics menu uses, and the reason is arithmetic: the
 * stash-box rules are forty-three settings, and a pane that draws forty-three three-way menus is a
 * pane nobody reads a single row of.
 *
 * ## Why the state is here and not in the group
 *
 * Because what has to change is the PANE, and the group is inside it. Seven groups each reaching
 * out to replace their own pane would be seven copies of the same logic and seven chances for a
 * back arrow to behave differently. `SettingsPane` reads this and draws one thing or the other, so
 * the mechanism exists once and every pane has it without knowing.
 *
 * ## Why the open pane is HIDDEN rather than unmounted
 *
 * The sub-page's content is a snippet belonging to the component that declared it. Unmount that
 * component and the snippet goes with it, taking the rows, their loaded values and anything
 * half-typed. So the pane stays mounted and is hidden with `display: none`, which also takes it out
 * of the tab order and out of the accessibility tree. See `SettingsPane`.
 *
 * ## Why a group declares the KEYS it owns
 *
 * So a deep link still works. `/settings/stash-boxes#enrich.person.name` names a setting that is
 * now one level down, and `$lib/settings-ui/settings-anchor` has no way to know that. It asks here, the group
 * that owns the key opens itself, and the hunt for the row then finds it on the sub-page.
 *
 * It is the register of WHATEVER HIDES A ROW, not only of sub-pages. A fold that draws its rows
 * only while it is open (a Details block, a mode) claims its keys here with the call that opens
 * it, and a deep link then opens it the same way. (A native `<details>` needs nothing: its rows are
 * in the document while it is shut, and the hunt opens it itself.)
 */

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

	/**
	 * Claim a set of setting keys, and say how to open the page they are on.
	 *
	 * Returns the release, for `onDestroy`. A group that unmounted without releasing would leave a
	 * closure over a dead component behind, and the next deep link would call it.
	 */
	own(keys: readonly string[], open: () => void): () => void {
		for (const key of keys) this.#owners.set(key, open);
		return () => {
			for (const key of keys) {
				if (this.#owners.get(key) === open) this.#owners.delete(key);
			}
		};
	}

	/**
	 * Claim every key on a SECTION that nothing claims by name, and say how to open the place they
	 * are drawn. For a pane of tabs whose first tab draws rows it cannot list ahead of time (one per
	 * task, from the server): a link to one of them, followed while another tab is showing, opens
	 * that tab, and the hunt then finds the row the ordinary way.
	 */
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

/**
 * The keys a pane's search declaration files on one of its sub-pages: the rows the page draws by
 * hand, which the registry knows nothing about.
 *
 * Read from the declaration rather than listed again beside the page. A row drawn on a sub-page
 * has to be in the settings index (or a search and a pasted path cannot find it) and has to be
 * claimed by the page (or a deep link to it stops at the pane), and those are one fact: the entry
 * says which page the row is on, and the page claims whatever says it. A second list is the one
 * that gets forgotten. `test_every_settings_sub_page_row_can_be_found.py` holds each row drawn on a page to
 * an entry filed under that page's title.
 */
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

/**
 * What a page claims for a thing it draws that has a name and no key: a heading over a group, a
 * card, a row drawn by hand. A result naming one opens the page through the same claim a key does
 * (`Drilldown.own`), and the name is then looked for on it.
 */
export function byName(name: string): string {
	return `name:${name.trim().toLowerCase()}`;
}
