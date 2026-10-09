<script lang="ts" module>
	import type { Snippet } from 'svelte';
	import type { SelectOption } from '$lib/components/common';

	/** A block of settings drawn together: a heading, one sentence, and the keys in reading order. */
	export interface SettingBlock {
		heading?: string;
		/** One sentence for the whole block, where its rows differ only by which one they are. */
		help?: string;
		keys: string[];
		/** Rows that mean nothing unless another setting is on: the row's key, and the key it
		 * hangs off. */
		dependsOn?: Record<string, string>;
		/** The picture beside each answer of a menu row, by the row's key: see `SettingRow`'s `preview`. */
		pictures?: Record<string, Snippet<[SelectOption]>>;
		/** The words an answer of a menu row shows when highlighted, by the row's key. */
		tooltips?: Record<string, (value: string) => string | undefined>;
	}
</script>

<script lang="ts">
	/* A pane's settings, in the order somebody reads them. */
	import SettingGroup from './SettingGroup.svelte';
	import SettingRow from './SettingRow.svelte';
	import type { SettingsPanel } from './panel.svelte';

	interface Props {
		panel: SettingsPanel;
		blocks: SettingBlock[];
		/** Rows that should not accept a change yet: a feature that is switched off, say. */
		disabled?: boolean;
	}

	let { panel, blocks, disabled = false }: Props = $props();

	/** Whether a row's row-it-hangs-off is switched off. False when it hangs off nothing. */
	function dependencyUnmet(map: Record<string, string> | undefined, key: string): boolean {
		const on = map?.[key];
		return on !== undefined && panel.value(on) !== true;
	}

	const drawn = $derived(
		blocks
			.map((block) => ({ ...block, entries: panel.pick(...block.keys) }))
			.filter((block) => block.entries.length > 0)
	);
</script>

{#each drawn as block, at (`${at}:${block.heading ?? block.keys[0]}`)}
	<SettingGroup heading={block.heading} help={block.help}>
		{#each block.entries as entry (entry.key)}
			<SettingRow
				{entry}
				disabled={disabled || dependencyUnmet(block.dependsOn, entry.key)}
				value={panel.value(entry.key)}
				showHelp={!block.help}
				preview={block.pictures?.[entry.key]}
				tooltip={block.tooltips?.[entry.key]}
				onchange={(next) => panel.save(entry.key, next)}
			/>
		{/each}
	</SettingGroup>
{/each}
