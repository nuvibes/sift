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
	   the veil for the surfaces that are NOT a dialog (the file view, the settings panel, the
	   search overlay), which own their opening and closing and want only the sheet. */

	/*
	 * The dimmed sheet over the whole window, whose only job is to swallow a click and close what is
	 * in front of it.
	 *
	 * ## Why it is a button, and why it is not the shared Button
	 *
	 * It is a `<button>` so that the click is reachable from a keyboard and announced as closing
	 * ("Close settings", "Close search") rather than a `<div>` that only a pointer can use. It is
	 * NOT the shared `Button`, which is a control you can see; this has no words, no glyph and no
	 * shape of its own. One component, so that reasoning and the rule it needs are written once
	 * rather than at every surface that wants a veil.
	 *
	 * ## What is here and what is not
	 *
	 * The ground, the blur and the fixed position are the global `.veil` rule in `app.css`, which
	 * `Modal`'s overlay wears too, so a dialog's veil and a panel's veil cannot come apart. What
	 * differs is the LAYER: the file view sits under the settings panel, which sits under a dialog,
	 * and each layer's veil has a z-index token of its own. The layer is the one thing a caller
	 * says. The fade in and out is the shared `veil` transition.
	 */
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
