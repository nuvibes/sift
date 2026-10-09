<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Veil',
		category: 'surface',
		role: 'the dimmed sheet over the window behind a panel, which closes the panel when pressed',
		basis: 'site:<button>',
		states: ['asset', 'settings', 'dialog']
	} satisfies DesignEntry;

	/** Which layer the veil sits on. Each names a z-index token the layout owns. */
	export type VeilLayer = 'asset' | 'settings' | 'dialog';
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: the library's Dialog has an overlay of its own, and `Modal` uses it. This is
	the veil for surfaces that are not a dialog, which want only the sheet. */

	/* The dimmed sheet that swallows a click and closes what is in front: a real button so the
	   keyboard reaches it; the look is `.veil` in app.css, the layer the caller's. */
	import { veil } from '$lib/shell/motion.svelte';

	interface Props {
		/** What pressing it does, for a screen reader: "Close settings". */
		label: string;
		onclose: () => void;
		layer?: VeilLayer;
	}

	let { label, onclose, layer = 'asset' }: Props = $props();
</script>

<!-- DRESSED BY: .veil (app.css draws the sheet every veil in the app is; this file adds only the layer) -->
<button
	type="button"
	class="veil"
	style:z-index="var(--z-{layer}-veil)"
	aria-label={label}
	onclick={onclose}
	transition:veil
></button>

<style>
	/* A button with none of a button's dressing. The sheet itself is `.veil` in app.css. */
	button {
		border: 0;
		padding: 0;
		cursor: default;
	}
</style>
