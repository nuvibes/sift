<script lang="ts">
	/*
	 * Which browser a link out of Sift opens in.
	 *
	 * On General: it is what happens on this device when you press a link (not how Sift looks,
	 * and not a connection), which is exactly what General is for. An address naming its older
	 * place still lands on it: see `KEY_MOVED_TO` in `sections.ts`.
	 *
	 * Only the desktop client can answer, and in a browser the whole block is absent: a browser
	 * already IS the answer to this question.
	 */
	import { onMount } from 'svelte';
	import { Select } from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import { bridge, type BrowserChoice } from '$lib/bridge';
	import SettingGroup from './SettingGroup.svelte';
	import { LINKS_OPEN_IN } from './LinksOpenIn.search';

	let links = $state<BrowserChoice>({ chosen: null, browsers: [] });

	/** The system default first, because it is what happens when nobody has chosen. */
	const SYSTEM_DEFAULT = 'system';

	onMount(() => {
		if (bridge.canChooseBrowser()) void bridge.browsers().then((state) => (links = state));
	});

	const browserOptions = $derived([
		{ value: SYSTEM_DEFAULT, label: 'Default browser' },
		...links.browsers.map((one) => ({ value: one.id, label: one.name }))
	]);

	async function chooseBrowser(value: string) {
		links = await bridge.setBrowser(value === SYSTEM_DEFAULT ? null : value);
	}
</script>

{#if bridge.canChooseBrowser() && links.browsers.length > 0}
	<SettingGroup
		id="general.links_open_in"
		heading="Links"
		help="A link to another website opens in your browser, with your extensions."
	>
		<LabelledRow
			label={LINKS_OPEN_IN.name}
			help="Default browser is the one Windows opens links with. This changes it for Sift only."
		>
			<Select
				label={LINKS_OPEN_IN.name}
				value={links.chosen ?? SYSTEM_DEFAULT}
				options={browserOptions}
				onValueChange={(next: string) => void chooseBrowser(next)}
			/>
		</LabelledRow>
	</SettingGroup>
{/if}
