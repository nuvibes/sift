<script lang="ts">
	/*
	 * What the window's close button does.
	 *
	 * On General, beside the browser choice, because it is the same kind of question: what this
	 * application does on THIS device when you press something, rather than anything about the
	 * library. Somebody who moves their library to another device takes neither answer with them.
	 * An address naming its old place still lands on it (`KEY_MOVED_TO`).
	 *
	 * Only the desktop app can answer. In a browser the whole block is absent: a browser tab has
	 * no notification area and closing it was never going to stop anything running.
	 *
	 * Stored by the shell of the computer this window is on (its settings file), never by the
	 * server. In the app in client mode that is the computer in front of the person, so closing
	 * this window there never decides whether Sift keeps running on the computer holding the
	 * library; General names this computer above these rows.
	 */
	import { onMount } from 'svelte';
	import { Switch } from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import { bridge } from '$lib/bridge';
	import SettingGroup from './SettingGroup.svelte';
	import { KEEP_RUNNING } from './ClosingTheWindow.search';

	/* Null until the shell has answered, and the row is not drawn before then. A switch showing
	   off while the question is still in flight is a switch that says the wrong thing first. */
	let keeping = $state<boolean | null>(null);

	const offered = bridge.canKeepRunningWhenClosed();

	onMount(() => {
		if (offered) void bridge.keepRunningWhenClosed().then((answer) => (keeping = answer));
	});

	async function setIt(on: boolean) {
		/* Shown at once and then corrected by what the shell answers. The write is a local one that
		   cannot fail in any interesting way, and a switch that waits for a round trip before moving
		   reads as a switch that did not take. */
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
