/* What every settings pane needs: the declarations, the values, and a write that can be undone. */

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
		/* Re-read when a setting moves somewhere else: this account on a second browser, or
		 * another admin changing one the whole installation shares. */
		whenChanged(settingChanges, () => void this.load());
	}

	/* Whether the pane has drawn an answer: a re-read after that is in place, never a loading state. */
	#drawn = false;

	/* When each key's last write landed (endless while it is out): a re-read asked before then
	   may answer the value from before it, so that key keeps the one drawn. */
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

	/** The declarations for named keys, in the order asked for, skipping any not on offer. */
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
