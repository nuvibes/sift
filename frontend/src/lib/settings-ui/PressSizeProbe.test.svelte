<script lang="ts">
	/* The holders a settings press stands in, each with a press named by what it is, for the
	   press size test. */
	import Button from '$lib/components/common/Button.svelte';
	import ChooseFile from '$lib/components/common/ChooseFile.svelte';
	import FormCard from '$lib/components/common/FormCard.svelte';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import Select from '$lib/components/common/Select.svelte';
	import SplitButton from '$lib/components/common/SplitButton.svelte';
</script>

<LabelledRow label="A row">
	<Button data-press="row">Edit</Button>
	<ChooseFile label="Choose" onchoose={() => {}}>Choose a file</ChooseFile>
	<Button data-press="named" size="medium">Named</Button>
	<Button data-press="row-submit" type="submit">Use</Button>
</LabelledRow>

<LabelledRow label="A choice and its press" besideField>
	<Select label="When" value="a" options={[{ value: 'a', label: 'A' }]} onValueChange={() => {}} />
	<SplitButton
		onclick={() => {}}
		trailingIcon="schedule"
		trailingLabel="Later"
		ontrailing={() => {}}>Run</SplitButton
	>
</LabelledRow>

<FormCard onsubmit={() => {}}>
	<Button data-press="form-body">Preset</Button>
	{#snippet actions()}
		<Button data-press="cancel">Cancel</Button>
		<Button data-press="submit" type="submit">Save</Button>
	{/snippet}
</FormCard>

<LabelledRow label="A row that opens a dialog">
	<Button data-press="opener">Open</Button>
	<Modal open title="A dialog" onOpenChange={() => {}}>
		<Button data-press="dialog">Inside</Button>
	</Modal>
</LabelledRow>

<Button data-press="alone">Alone</Button>
