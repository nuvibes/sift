/* What every settings pane needs: the declarations, the values, and a write that can be undone.
 *
 * Once, here, rather than in every pane: loading the settings, pulling out the keys a pane cares
 * about, holding each in its own piece of state, writing it and putting the old value back on a
 * refusal is twenty lines that drift apart when copied: a fix to one copy is a fix to one copy.
 *
 * A pane asks for a section and gets the entries the server declared for it, in the order they
 * were declared, with their labels, help, choices, bounds and units attached, which is what
 * lets a row derive its own control instead of the pane deciding.
 *
 * The write is optimistic and reversible: the new value shows immediately, and a refusal from the
 * server puts the old one back and says so. A settings screen that waits for a round trip before
 * moving a switch feels broken on a slow connection, and one that moves the switch and quietly
 * keeps the old value is worse.
 */

import {
	fetchSettings,
	saveSettings,
	type SettingEntry,
	type SettingSection
} from '$lib/settings-ui/settings';
import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
import { toasts } from '$lib/shell/toasts.svelte';

export class SettingsPanel {
	sections = $state<SettingSection[]>([]);
	loading = $state(true);
	/** True when the settings could not be read at all. A pane draws its own line about it. */
	failed = $state(false);

	/** Live values by key, so a row can be given one and a rollback can put one back. */
	#values = $state<Record<string, unknown>>({});

	constructor() {
		/* Re-read when a setting moves somewhere else: this account on a second browser, or another
		 * admin changing one the whole installation shares.
		 *
		 * Here rather than in each pane, because there are a dozen panes and the twelfth is the one
		 * somebody forgets. Every one of them builds one of these during its own setup, which is what
		 * gives this an effect to live in and what takes it away again with the pane.
		 */
		whenChanged(settingChanges, () => void this.load());
	}

	/* Whether the pane has drawn an answer: a re-read after that is in place, never a loading state. */
	#drawn = false;

	/* When each key's last write landed (endless while it is out): a re-read asked before then may
	   answer the value from before it, so that key keeps the one drawn. */
	#stamp = 0;
	#written = new Map<string, number>();

	async load(): Promise<void> {
		const asked = this.#stamp;
		const first = !this.#drawn;
		if (first) {
			this.loading = true;
			this.failed = false;
		}
		try {
			const sections = await fetchSettings();
			if (JSON.stringify(sections) === JSON.stringify(this.sections)) return;
			this.sections = sections;
			const values: Record<string, unknown> = {};
			for (const section of sections) {
				for (const entry of section.settings ?? []) values[entry.key] = entry.value;
			}
			for (const [key, stamp] of this.#written) {
				if (stamp > asked) values[key] = this.#values[key];
				else this.#written.delete(key);
			}
			this.#values = values;
		} catch {
			// A re-read that fails leaves the pane as drawn.
			if (first) this.failed = true;
		} finally {
			if (!this.failed) this.#drawn = true;
			this.loading = false;
		}
	}

	/** Every setting declared into a section, or an empty list when the caller may not see it. */
	in(section: string): SettingEntry[] {
		return this.sections.find((one) => one.name === section)?.settings ?? [];
	}

	/** The declarations for named keys, in the order asked for, skipping any not on offer.
	 *
	 * Skipping rather than failing is what hides an instance-wide setting from a guest: the server
	 * leaves it out of the response entirely, and a pane that named it simply draws one row fewer.
	 */
	pick(...keys: string[]): SettingEntry[] {
		const all = new Map(
			this.sections.flatMap((section) => section.settings ?? []).map((one) => [one.key, one])
		);
		return keys.map((key) => all.get(key)).filter((one): one is SettingEntry => one !== undefined);
	}

	entry(key: string): SettingEntry | undefined {
		return this.pick(key)[0];
	}

	value(key: string): unknown {
		return this.#values[key];
	}

	/** Write one setting. Shows the new value immediately, and puts the old one back if it is refused. */
	async save(key: string, next: unknown): Promise<void> {
		const previous = this.#values[key];
		this.#written.set(key, Number.POSITIVE_INFINITY);
		this.#values = { ...this.#values, [key]: next };
		try {
			await saveSettings({ [key]: next });
		} catch {
			this.#values = { ...this.#values, [key]: previous };
			toasts.show("That couldn't be saved", { tone: 'error' });
		} finally {
			this.#written.set(key, ++this.#stamp);
		}
	}
}
