<script lang="ts">
	/* DRESSED BY: KeptPill (the pill, the bubble, the three dots and the right-click are all its:
	   this file declares what a saved WALL's verbs are and what its bubble draws). */

	/* One Saved Layout, as the same pill a kept filter is; its bubble is the layout's snapshot. */
	import KeptPill from '$lib/components/common/KeptPill.svelte';
	import LayoutSnapshot from './LayoutSnapshot.svelte';
	import type { Preset } from '$lib/theater/presets.svelte';
	import type { Verb } from '$lib/components/common/verbs';

	interface Props {
		kept: Preset;
		onopen: (kept: Preset) => void;
		onupdate?: (kept: Preset) => void;
		onrename?: (kept: Preset) => void;
		onremove?: (kept: Preset) => void;
		/** A write against this one is in flight. See `KeptPill.busy`, which is what draws it. */
		busy?: boolean;
	}

	let { kept, onopen, onupdate, onrename, onremove, busy = false }: Props = $props();

	const verbs = $derived.by(() => {
		const rows: Verb[] = [];
		if (onrename) {
			rows.push({
				id: 'rename',
				label: 'Rename',
				icon: 'edit_square',
				group: 'change',
				singleOnly: true,
				run: () => onrename(kept)
			});
		}
		if (onupdate) {
			rows.push({
				id: 'update',
				label: 'Update from the wall on screen',
				icon: 'sync',
				group: 'change',
				singleOnly: true,
				run: () => onupdate(kept)
			});
		}
		if (onremove) {
			rows.push({
				id: 'remove',
				label: 'Delete',
				icon: 'delete',
				destructive: true,
				singleOnly: true,
				run: () => onremove(kept)
			});
		}
		return rows;
	});
</script>

<KeptPill
	wide
	id={kept.id}
	name={kept.name}
	{verbs}
	{busy}
	onapply={() => onopen(kept)}
	holds={preview}
/>

{#snippet preview()}
	<LayoutSnapshot wall={kept} />
{/snippet}
