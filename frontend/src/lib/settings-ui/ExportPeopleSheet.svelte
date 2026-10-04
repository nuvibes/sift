<script lang="ts">
	/* Settings > Faces: the sheet behind Choose people on the export row, which says who a file of
	 * facial fingerprints carries. Two ways, chosen at its head and remembered for the account:
	 * everyone ticked and the picks left out, or nobody ticked and only the picks sent. The list is
	 * read when it opens, so it is the list as it stands, not as the pane's search box narrowed it.
	 */
	import {
		ChoiceCard,
		ChoiceGroup,
		PickDialog,
		type Choice,
		type PickChoice
	} from '$lib/components/common';
	import { knownPeople } from '$lib/people/faces.svelte';
	import { personRow } from '$lib/people/person-row';
	import { thinChoice } from '$lib/people/thin-fingerprints';
	import {
		exportWay,
		recallInterfaceState,
		rememberExportWay,
		type ExportWay
	} from '$lib/shell/interface-state.svelte';
	import { COPY } from './Faces.search';

	interface Props {
		/** Save the file with these People in it. */
		onsend: (personIds: string[]) => void;
		/** What the pane's problem line says, or null to clear it. */
		onproblem: (message: string | null) => void;
	}

	let { onsend, onproblem }: Props = $props();

	let open = $state(false);
	let way = $state<ExportWay>('except');
	let choices = $state<PickChoice[]>([]);

	/* The list is read before the sheet opens, because the sheet asks who is ticked as it opens,
	   and so is the way it last opened, which decides that. */
	export async function choose() {
		onproblem(null);
		try {
			await recallInterfaceState();
			way = exportWay();
			choices = (await knownPeople('')).items
				.filter((one) => one.id && one.faces > 0)
				.map((person) => thinChoice(personRow(person), person));
			open = true;
		} catch {
			onproblem("That list couldn't be loaded. Try again.");
		}
	}

	/* Who is ticked as the sheet opens: the ticks are who goes in, so everyone where a press leaves
	   out, and nobody where a press puts in. */
	async function tickedAtFirst(): Promise<Record<string, 'all'>> {
		return way === 'except'
			? Object.fromEntries(choices.map((one) => [one.id, 'all' as const]))
			: {};
	}

	/* The choice at the sheet's head, remembered at once; the sheet starts over on it. */
	function chooseWay(next: string) {
		way = next === 'only' ? 'only' : 'except';
		rememberExportWay(way);
	}

	/* How many the file would carry if the sheet's Export were pressed now. */
	function going(on: number, off: number): number {
		return way === 'except' ? choices.length - off : on;
	}

	/* Everyone but the People left out, from the sheet opened with everyone ticked. */
	function sendAllBut(leftOut: Choice[]) {
		const out = new Set(leftOut.map((one) => one.id));
		const personIds = choices.filter((one) => !out.has(one.id)).map((one) => one.id);
		if (personIds.length === 0) {
			onproblem(COPY.pack.nobodyLeft);
			return;
		}
		onsend(personIds);
	}
</script>

<PickDialog
	bind:open
	title={COPY.pack.pickTitle}
	subject={COPY.pack.pickSubject(way, choices.length)}
	{choices}
	placeholder="Who"
	already={tickedAtFirst}
	restart={way}
	confirmLabel={(on: number, off: number) => COPY.pack.pickConfirm(going(on, off))}
	onpick={(picked: Choice[]) => {
		if (way === 'only') onsend(picked.map((one) => one.id));
	}}
	onunpick={(leftOut: Choice[]) => {
		if (way === 'except') sendAllBut(leftOut);
	}}
>
	{#snippet head()}
		<!-- The two ways, at the head because each decides which rows start ticked. -->
		<ChoiceGroup label={COPY.pack.wayLabel} layout="column" value={way} onchange={chooseWay}>
			<ChoiceCard value="except" name={COPY.pack.ways.except} />
			<ChoiceCard value="only" name={COPY.pack.ways.only} />
		</ChoiceGroup>
	{/snippet}
</PickDialog>
