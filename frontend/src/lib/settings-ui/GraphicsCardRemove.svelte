<script lang="ts">
	/* Deleting GPU support: the way out, as the pane's last group, the place every settings pane
	 * keeps the one act that takes something away. */
	import ActionRow from './ActionRow.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import { COPY } from './GraphicsCard.search';
	import type { GraphicsCardState } from './graphics-card-state.svelte';
	import { explainAbsentRows } from '$lib/settings-ui/settings-anchor.svelte';

	const NOT_DOWNLOADED = "GPU support isn't downloaded, so there's nothing to delete.";

	let { card }: { card: GraphicsCardState } = $props();

	/* Drawn only while GPU support is downloaded: a link landing on it otherwise rings the
	   card's own block, which says whether there is any. */
	$effect(() =>
		explainAbsentRows((key) =>
			key === 'performance.gpu-remove' && card.accel !== null && !card.removable
				? { because: NOT_DOWNLOADED, near: 'performance.graphics_card' }
				: null
		)
	);
</script>

{#if card.removable}
	<SettingGroup id="performance.gpu-remove" heading={COPY.remove.heading} help={COPY.remove.lede}>
		<ActionRow
			label={COPY.remove.label}
			help={COPY.remove.help}
			action={COPY.remove.action}
			destructive
			busy={card.removing}
			onclick={() => void card.remove()}
		/>
	</SettingGroup>
{/if}
