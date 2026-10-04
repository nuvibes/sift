<script lang="ts">
	/* A settings pane in miniature, for the line test: three groups, rows side by side, rows held
	   one level down (each task's block on Tasks), and a group of one row. */
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import ActionRow from './ActionRow.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import SectionHeading from '$lib/components/common/SectionHeading.svelte';
</script>

<div class="section-stack">
	<SettingGroup heading="First">
		<LabelledRow label="One">a</LabelledRow>
		<ActionRow label="Two" action="Go" onclick={() => {}} />
		<LabelledRow label="Three">c</LabelledRow>
	</SettingGroup>
	<SettingGroup heading="Second">
		<div class="probe-task"><LabelledRow label="Four">d</LabelledRow></div>
		<div class="probe-task">
			<LabelledRow label="Five">e</LabelledRow>
			<LabelledRow label="Six">f</LabelledRow>
		</div>
	</SettingGroup>
	<SettingGroup heading="Third">
		<LabelledRow label="Seven">g</LabelledRow>
	</SettingGroup>
	<!-- Blocks that hold their rows directly, each opening with its own heading, the second with a
	     sentence before its rows (Appearance's blocks): the row after the sentence carries no line,
	     though the block before it ends on a row. -->
	<section class="probe-block">
		<SectionHeading>Fourth</SectionHeading>
		<LabelledRow label="Eight">h</LabelledRow>
	</section>
	<section class="probe-block">
		<SectionHeading>Fifth</SectionHeading>
		<p class="probe-note">A sentence about the rows below.</p>
		<LabelledRow label="Nine">i</LabelledRow>
		<LabelledRow label="Ten">j</LabelledRow>
	</section>
</div>
