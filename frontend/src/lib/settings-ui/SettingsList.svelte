<script lang="ts" module>
	import type { Snippet } from 'svelte';
	import type { SelectOption } from '$lib/components/common';

	/** A block of settings drawn together: a heading, one sentence, and the keys in reading order. */
	export interface SettingBlock {
		heading?: string;
		/**
		 * One sentence for the whole block, where its rows differ only by which one they are.
		 *
		 * Given, the rows inside are drawn without their own help, which is the rule about
		 * repetition, enforced in one place instead of remembered in thirteen.
		 */
		help?: string;
		keys: string[];
		/**
		 * Rows that mean nothing unless another setting is on: the row's key, and the key it hangs
		 * off.
		 *
		 * Greyed out rather than hidden. A row that disappears takes the knowledge that it exists
		 * with it: somebody who has turned a feature off cannot see what turning it back on would
		 * give them, and a setting they configured once is simply gone. Left visible and inert, the
		 * relationship between the two is on screen.
		 */
		dependsOn?: Record<string, string>;
		/** The picture beside each answer of a menu row, by the row's key: see `SettingRow`'s `preview`. */
		pictures?: Record<string, Snippet<[SelectOption]>>;
		/** The words an answer of a menu row shows when highlighted, by the row's key. */
		tooltips?: Record<string, (value: string) => string | undefined>;
	}
</script>

<script lang="ts">
	/* A pane's settings, in the order somebody reads them.
	 *
	 * Names the keys and nothing else. What each row is called, what it says, whether it is a menu
	 * or a number or a toggle, what its bounds are and what its options are called all come from the
	 * setting's own declaration, so adding a setting to a pane is one key in one list here, and a
	 * setting can never be drawn with a control that disagrees with what it holds.
	 *
	 * A key the server did not send is simply not drawn. That is how an instance-wide setting stays
	 * out of a guest's view: the response leaves it out, and the pane shows one row fewer rather
	 * than a second rule about who may see what.
	 */
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
