<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'PressSize',
		category: 'composition',
		role: 'gives the presses inside it one height, where a holder draws two kinds of press',
		basis: 'own',
		states: ['default']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* NOT ON THE GALLERY: it draws no element of its own. `FormCard`, which the gallery draws,
	 * WHY NOT BITS-UI: bits-ui has no such primitive: this sets a context and draws nothing of its own.
	   holds its answers in one. */
	/*
	 * Gives the presses inside it one size, for a holder that draws two kinds of press: a form's
	 * body (the small row press) and its answers (the default size, Cancel beside the submit).
	 * See `press-size`.
	 */
	import type { Snippet } from 'svelte';
	import type { ButtonSize } from './Button.svelte';
	import { givePressSize } from './press-size';

	let { size, children }: { size: ButtonSize | undefined; children: Snippet } = $props();

	// svelte-ignore state_referenced_locally
	givePressSize(size);
</script>

{@render children()}
