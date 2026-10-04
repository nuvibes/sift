<script lang="ts">
	/* A harness for `Note`, which takes its sentence as children, and a snippet cannot be handed
	   to `mount` from a test file. The same arrangement `ShellBannerProbe` uses. */
	import Note, { type NoteTone } from './Note.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		tone?: NoteTone;
		icon?: IconName;
		words?: string;
	}

	let { tone, icon, words = 'A thing worth knowing.' }: Props = $props();
</script>

{#if tone !== undefined && icon !== undefined}
	<Note {tone} {icon}>{words}</Note>
{:else if tone !== undefined}
	<Note {tone}>{words}</Note>
{:else}
	<Note>{words}</Note>
{/if}
