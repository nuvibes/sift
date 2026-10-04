<script lang="ts">
	/* A pane for the read-only tests: holds itself as the named section would, and draws one row of
	   each kind a device pane is made of. */
	import Button from '$lib/components/common/Button.svelte';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import ActionRow from './ActionRow.svelte';
	import { holdPane } from './read-only';

	let { section }: { section: string } = $props();
	holdPane(() => section);
</script>

<LabelledRow label="Keep this many" help="How many backups are kept.">
	<Button>7</Button>
</LabelledRow>
<ActionRow
	label="Optimize the database"
	help="Frees disk space."
	note="120 MB to free"
	action="Optimize"
	onclick={() => {}}
/>
