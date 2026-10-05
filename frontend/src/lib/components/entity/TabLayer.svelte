<script lang="ts" module>
	import { getContext, setContext, type Snippet } from 'svelte';

	const HELD = Symbol('tab-held');

	/** Whether this wall is the one `TabHold` is letting go: a held wall leaves the top bar alone. */
	export function wallHeld(): () => boolean {
		return getContext<(() => boolean) | undefined>(HELD) ?? (() => false);
	}
</script>

<script lang="ts">
	/* NOT ON THE GALLERY: it draws nothing of its own, only the wall `TabHold` hands it. */
	let { held, children }: { held: boolean; children: Snippet } = $props();

	setContext(HELD, () => held);
</script>

{@render children()}
