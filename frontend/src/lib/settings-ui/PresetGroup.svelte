<script lang="ts">
	/* Several settings that are all the same question: asked once, with a way in to each. */
	import type { Snippet } from 'svelte';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import { Button, Select, Switch } from '$lib/components/common';
	import SettingGroup from './SettingGroup.svelte';
	import { drilldown } from './drilldown.svelte';

	/** The reading shown when the children disagree. Never a value anything can be set to. */
	const MIXED = '__mixed__';

	/** The press that opens the page, and the crumb it adds to every settings path on it. */
	const EDIT = 'Edit';

	interface Props {
		heading: string;
		/** One sentence for the whole block, in place of the same sentence on every child. */
		help?: string;
		/** What the one control is called. */
		label: string;
		/** What choosing on it does, said once. */
		masterHelp?: string;
		/** A menu master: the choices, and what every child holds. See the three masters above. */
		options?: { value: string; label: string }[];
		/** What every child holds, or null where they differ. */
		shared?: string | null;
		/** Set every child in one go. */
		onchoose?: (value: string) => void;
		/** A switch master: the group's own answer, and what to do when it is flipped. */
		on?: boolean | null;
		onswitch?: (on: boolean) => void;
		/** A fixed master: what to say where there is nothing to choose. */
		fixed?: string;
		/** What the sub-page is called, at the top of it and on the way back. */
		pageTitle: string;
		/** The row that opens it: "Set each field", "Set each one". */
		editLabel: string;
		/** The settings on the sub-page, so a deep link to one of them can open the page first. */
		keys?: readonly string[];
		/** The individual rows. Ordinary setting rows; this only decides where they are drawn. */
		children: Snippet;
		disabled?: boolean;
		/** Rows of the group's own, drawn under the Edit row: the act that goes with the
		 * question. */
		actions?: Snippet;
	}

	let {
		heading,
		help,
		label,
		masterHelp,
		options,
		shared,
		onchoose,
		on = undefined,
		onswitch,
		fixed,
		pageTitle,
		editLabel,
		keys = [],
		children,
		disabled = false,
		actions
	}: Props = $props();

	/* Refused loudly rather than resolved by precedence. */
	const masters = $derived(
		[options !== undefined, on !== undefined, fixed !== undefined].filter(Boolean).length
	);
	$effect(() => {
		if (masters !== 1) {
			throw new Error(
				`the "${heading}" group needs exactly one of options, on or fixed: it has ${masters}`
			);
		}
	});

	function openPage(): void {
		drilldown.open(pageTitle, children, EDIT);
	}

	/* Claimed in an effect, not once at setup, and that is not tidiness. */
	$effect(() => drilldown.own(keys, openPage));

	/* `Custom` is appended only while it is the true reading, so it cannot be chosen from a
	   state where it would mean nothing. */
	const shownOptions = $derived(
		shared === null ? [...(options ?? []), { value: MIXED, label: 'Custom' }] : (options ?? [])
	);
</script>

<SettingGroup {heading} {help}>
	<LabelledRow {label} help={masterHelp}>
		{#if fixed !== undefined}
			<!-- No control, because there is no choice. A disabled switch reads as something that
			     could be turned off if only you had permission, which is a different statement. -->
			<span class="fixed">{fixed}</span>
		{:else if on !== undefined}
			<Switch
				checked={on ?? false}
				{label}
				disabled={disabled || on === null}
				onCheckedChange={(next) => onswitch?.(next)}
			/>
		{:else}
			<Select
				value={shared ?? MIXED}
				options={shownOptions}
				{label}
				{disabled}
				onValueChange={(next) => {
					/* Choosing the reading is not a change. It can only be reached by opening the menu
					   on a mixed group and picking the row that says what is already true. */
					if (next !== MIXED) onchoose?.(next);
				}}
			/>
		{/if}
	</LabelledRow>

	<!-- An ordinary row whose control is a button, so it lines up with every other row on the pane
	     and needs no rule of its own. The button says Edit for the same reason every one of these
	     does: the row's name is what is being edited. -->
	<LabelledRow label={editLabel} help="Give any one of them a different answer.">
		<Button icon="edit" {disabled} onclick={openPage}>{EDIT}</Button>
	</LabelledRow>
	{@render actions?.()}
</SettingGroup>

<style>
	/* Reads as a value, not as something to press. Same ink as a fact on the About pane, because
	   that is what it is. */
	.fixed {
		align-self: center;
		font: var(--text-body);
		color: var(--sift-ink-3);
	}
</style>
