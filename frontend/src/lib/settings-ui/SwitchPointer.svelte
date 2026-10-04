<script lang="ts" module>
	import type { Searchable } from '$lib/settings-ui/search';
	import type { SettingEntry } from '$lib/settings-ui/settings';

	/** The words this row writes itself. The switch's own name is its registered label. */
	export const COPY = {
		help: 'Turned on and off under Importing, with the other recognition switches.',
		on: 'On',
		off: 'Off',
		go: 'Change in Importing'
	} as const;

	/** Rows drawn only while on, by key, under a setting's label or the search entry's name. */
	export function namesOf(
		keys: readonly string[],
		entries: ReadonlyMap<string, SettingEntry>,
		searchable: readonly Searchable[]
	): Record<string, string> {
		const named: Record<string, string> = {};
		for (const key of keys) {
			const name =
				entries.get(key)?.label ?? searchable.find((one) => one.key === key)?.name ?? undefined;
			if (name) named[key] = name;
		}
		return named;
	}
</script>

<script lang="ts">
	/*
	 * Where a recognition feature's switch is, on the feature's own pane.
	 *
	 * Faces, Smart Search and watermark reading are switched on and off in ONE place, Settings >
	 * Importing > Recognition, where the three stand together beside the work they start. A second
	 * switch on each feature's pane would be two doors onto one setting; this row says whether it is on
	 * and takes somebody to the switch, and it carries the setting's own address, so an older link
	 * naming the switch on this pane still lands on the row that says where it went.
	 */
	import { SettingLink } from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import { explainAbsentRows, hiddenWhile } from '$lib/settings-ui/settings-anchor.svelte';

	interface Props {
		/** The switch's registered declaration: its key and its name. */
		entry: SettingEntry;
		/** Whether it is on now. */
		on: boolean;
		/** Rows drawn only while it is on (`namesOf`): a link to one rings this row instead. */
		hides?: Record<string, string>;
	}

	let { entry, on, hides = {} }: Props = $props();

	$effect(() =>
		explainAbsentRows((key) => {
			const row = hides[key];
			if (on || !row || !entry.label) return null;
			return { because: hiddenWhile(row, entry.label, COPY.off), near: entry.key };
		})
	);
</script>

<!-- The way to the switch first and the state last, so On or Off ends the row on its far edge,
     where every other row's value sits, and the link reads as the way to change it. -->
<LabelledRow id={entry.key} label={entry.label ?? entry.key} help={COPY.help} looseColumn>
	<SettingLink section="importing" setting={entry.key}>{COPY.go}</SettingLink>
	<span class="state">{on ? COPY.on : COPY.off}</span>
</LabelledRow>

<style>
	.state {
		color: var(--sift-ink-2);
		font: var(--text-body);
	}
</style>
