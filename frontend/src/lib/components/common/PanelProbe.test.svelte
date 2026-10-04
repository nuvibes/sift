<script lang="ts">
	/* A harness for `Panel`, which takes its contents as children, and a snippet cannot be handed
	   to `mount` from a test file. The same arrangement `NoteProbe` uses. */
	import Panel, { type PanelTone } from './Panel.svelte';

	interface Props {
		tone?: PanelTone;
		edge?: boolean;
		floating?: boolean;
		elevated?: boolean;
		words?: string;
	}

	let { tone, edge, floating, elevated, words = 'Something worth reading.' }: Props = $props();
</script>

{#if floating !== undefined || elevated !== undefined}
	<Panel floating={floating ?? false} elevated={elevated ?? false}>{words}</Panel>
{:else if tone !== undefined && edge !== undefined}
	<Panel {tone} {edge}>{words}</Panel>
{:else if tone !== undefined}
	<Panel {tone}>{words}</Panel>
{:else}
	<Panel>{words}</Panel>
{/if}
