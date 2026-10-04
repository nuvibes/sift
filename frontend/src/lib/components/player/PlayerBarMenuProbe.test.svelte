<script lang="ts">
	/*
	 * A bar whose drawer holds a menu the bar was never told about.
	 *
	 * A snippet cannot be handed to `mount` from a test file, so this is a component. The menu is
	 * an ordinary `MenuButton` with no hold wired to it: the bar has to notice it on its own.
	 */
	import MenuButton from '$lib/components/common/MenuButton.svelte';
	import ContextMenuItem from '$lib/components/common/ContextMenuItem.svelte';
	import PlayerBar from './PlayerBar.svelte';

	interface Props {
		onhold: () => void;
		onrelease: () => void;
	}

	let { onhold, onrelease }: Props = $props();
</script>

<PlayerBar
	position={0}
	duration={30}
	onseek={() => {}}
	playing={false}
	onplay={() => {}}
	muted={false}
	volume={1}
	onmute={() => {}}
	onvolume={() => {}}
	tray={drawer}
	{onhold}
	{onrelease}
/>

{#snippet drawer()}
	<MenuButton label="Any menu" words="Any menu">
		<ContextMenuItem label="First" onselect={() => {}} />
	</MenuButton>
{/snippet}
