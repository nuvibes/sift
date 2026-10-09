<script lang="ts">
	/* What the window's close button does. */
	import { onMount } from 'svelte';
	import { Switch } from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import { bridge } from '$lib/bridge';
	import SettingGroup from './SettingGroup.svelte';
	import { KEEP_RUNNING } from './ClosingTheWindow.search';

	/* Null until the shell has answered, and the row is not drawn before then. */
	let keeping = $state<boolean | null>(null);

	const offered = bridge.canKeepRunningWhenClosed();

	onMount(() => {
		if (offered) void bridge.keepRunningWhenClosed().then((answer) => (keeping = answer));
	});

	async function setIt(on: boolean) {
		/* Shown immediately and then corrected by what the shell answers. */
		keeping = on;
		keeping = await bridge.setKeepRunningWhenClosed(on);
	}
</script>

{#if offered && keeping !== null}
	<SettingGroup
		id="general.closing_the_window"
		heading="Closing the window"
		help="Sift can keep working while its window is closed: scans, downloads, and sharing your library with other devices."
	/>

	<LabelledRow
		label={KEEP_RUNNING.name}
		help="On by default. Closing the window leaves Sift in the notification area, where Open Sift brings it back and Quit Sift closes it. When off, closing the window quits Sift."
	>
		<Switch
			label={KEEP_RUNNING.name}
			checked={keeping}
			onCheckedChange={(on: boolean) => void setIt(on)}
		/>
	</LabelledRow>
{/if}
