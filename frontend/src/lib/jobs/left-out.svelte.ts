// SPDX-License-Identifier: AGPL-3.0-or-later
/* The files each product gave up on, from the build sheet the Import tasks draw: what a pass's
   "1 left out" counts and names. One read at a time. */
import { fetchBuildSheet } from '$lib/library/importing';

class LeftOut {
	/** Files given up on, by product key. */
	counts = $state<Record<string, number>>({});
	/** Each product's name, by its key. */
	labels = $state<Record<string, string>>({});
	#reading: Promise<void> | null = null;
	#seen: string | null = null;

	read(): Promise<void> {
		this.#reading ??= fetchBuildSheet()
			.then((sheet) => {
				this.counts = Object.fromEntries(sheet.rows.map((row) => [row.key, row.cannot]));
				this.labels = Object.fromEntries(sheet.rows.map((row) => [row.key, row.label]));
			})
			.catch(() => {})
			.finally(() => (this.#reading = null));
		return this.#reading;
	}

	/** Read again when which passes are moving changes: one that stops may have left a file out. */
	follow(moving: string): void {
		if (moving === this.#seen) return;
		this.#seen = moving;
		void this.read();
	}

	/** The products a pass names, with their names, for the page that lists their files. */
	named(keys: readonly string[]): { key: string; label: string }[] {
		return keys.map((key) => ({ key, label: this.labels[key] ?? key }));
	}
}

export const leftOut = new LeftOut();
