<script lang="ts">
	/*
	 * Unhide, as a button you can put on a page: the same job the top bar's control does, where
	 * somebody is standing when they need it, so the Hidden screen can take you to the vault rather
	 * than tell you where to go.
	 *
	 * Opening the vault costs the PIN every time, including when it is already open, which makes it
	 * a decision rather than a state somebody drifted into. Shutting it costs nothing and never
	 * fails.
	 */
	import { Button } from '$lib/components/common';
	import { vault, vaultPrompt } from '$lib/shell/vault.svelte';

	interface Props {
		/** Big and centred, for a screen whose whole content is behind this. */
		big?: boolean;
	}

	let { big = false }: Props = $props();

	function press() {
		if (vault.unlocked) {
			void vault.lock();
			return;
		}
		vaultPrompt.ask();
	}
</script>

<!-- Named for what pressing it will do: with no PIN yet, that is making one. -->
<Button
	size={big ? 'medium' : 'small'}
	icon={vault.unlocked ? 'visibility' : 'lock'}
	onclick={press}
>
	{vault.unlocked ? 'Hide' : vault.loaded && !vault.pinSet ? 'Create a PIN' : 'Unlock'}
</Button>

<style>
</style>
