<script lang="ts">
	/* The name of what is showing in Settings: a section, or a sub-page opened over one. */
	import Icon from '$lib/components/Icon.svelte';
	import type { IconName } from '$lib/design/icons';
	import PathCopy from './PathCopy.svelte';

	interface Props {
		label: string;
		icon?: IconName;
	}

	let { label, icon }: Props = $props();
</script>

<h1 class="settings-title path-host">
	<PathCopy />
	{#if icon}<Icon name={icon} size={20} />{/if}
	{label}
</h1>

<style>
	/* The display size, set once for every section there is. */
	.settings-title {
		position: relative;
		display: flex;
		align-items: center;
		gap: var(--space-2);
		margin: 0 0 var(--space-4);
		font: var(--text-display);
		letter-spacing: var(--tracking-display);
		color: var(--sift-ink);
	}

	/* The lit row's ink: `SettingsShell` dresses `.item.active` with the same token. */
	.settings-title :global(.icon) {
		color: var(--sift-accent-text);
	}
	/* The marker the copy press stands against: PathCopy places itself in the gutter of whatever
	   carries this class, so the class itself is what makes that place. */
	.path-host {
		position: relative;
	}
</style>
