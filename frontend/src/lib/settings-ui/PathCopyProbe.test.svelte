<script lang="ts">
	/* A section the way a pane draws one, for the settings path test: its title, a group with a
	   row and a press row, and a sub-page whose rows are declared HERE and drawn by the page. */
	import ActionRow from './ActionRow.svelte';
	import DrilldownPage from './DrilldownPage.svelte';
	import FactRow from './FactRow.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import SettingsTitle from './SettingsTitle.svelte';
	import { drilldown } from './drilldown.svelte';
	import { sectionPlace } from './settings-path';

	sectionPlace(() => 'Smart Search');
</script>

{#snippet more()}
	<SettingGroup heading="Models">
		<ActionRow label="Download the models again" action="Download" onclick={() => {}} />
	</SettingGroup>
{/snippet}

<SettingsTitle label="Smart Search" />
<SettingGroup heading="Auto-lock">
	<FactRow label="Lock Hidden when you switch away" fact="Off" />
	<ActionRow
		label="More settings"
		action="Edit"
		onclick={() => drilldown.open('More settings', more, 'Edit')}
	/>
</SettingGroup>
<DrilldownPage behind="Smart Search" />
