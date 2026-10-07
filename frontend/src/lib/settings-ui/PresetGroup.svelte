<script lang="ts">
	/*
	 * Several settings that are all the same question: asked once, with a way in to each.
	 *
	 * Not an inline `<details>` under the master control: opened on the stash-box rules that would
	 * unfold FORTY-THREE menus into the middle of the pane, which is the problem this solves
	 * happening one click later.
	 *
	 * ## The shape
	 *
	 * One control that sets all of them, and an Edit that replaces the pane with a page holding
	 * every one, with a way back. That is the shape a game's graphics menu uses (one Quality
	 * control, and a screen behind it where each of fourteen settings can disagree with it), and
	 * it is used here for every group of this kind rather than sometimes, because a pattern learned
	 * once costs nothing the second time.
	 *
	 * ## Why the master says "Custom" rather than lying
	 *
	 * A master control over children that disagree has no honest value to show. Picking one of them
	 * would say every child is set to it, which is false; showing the most common one is the same
	 * lie with arithmetic in front. So there is an extra option, it is only ever offered when it is
	 * already true, and choosing it is impossible: `Custom` is not selectable, it is a reading.
	 *
	 * ## Why the children are still real rows
	 *
	 * Because they are still real settings. What is on the sub-page is whatever the caller puts
	 * there (ordinary `SettingRow`s against the ordinary registry), so every gate that checks a
	 * setting reaches a screen still finds them, and the pane has not grown a second way of saving
	 * a value. This component owns the QUESTION, not the settings.
	 *
	 * ## The three masters, and why there is more than one
	 *
	 * The `Custom` reading above is what a master DERIVED from its children has to say when they
	 * disagree. Not every group is shaped that way, and forcing them all into it would put a menu
	 * where an honest switch belongs:
	 *
	 *   `options` + `shared`   a reading of the children. Says Custom when they differ.
	 *   `switch`               the group has a setting OF ITS OWN (does this run at all), which
	 *                          is a different question from what the children answer, so it has no
	 *                          mixed state and a switch cannot lie about it.
	 *   `fixed`                there is nothing to choose. Drawn as a word, so a group that cannot
	 *                          be turned off says so instead of offering a control that would have
	 *                          to be qualified.
	 *
	 * Exactly one of the three, and the component refuses more.
	 */
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
		/**
		 * What every child holds, or null where they differ.
		 *
		 * Worked out by the caller, which is the only place that knows which keys are in the group.
		 */
		shared?: string | null;
		/** Set every child in one go. */
		onchoose?: (value: string) => void;
		/**
		 * A switch master: the group's own answer, and what to do when it is flipped.
		 *
		 * `null` while it is still being read, so the switch is not drawn off and then flicked on,
		 * which reads as the setting having been changed by opening the screen.
		 */
		on?: boolean | null;
		onswitch?: (on: boolean) => void;
		/** A fixed master: what to say where there is nothing to choose. */
		fixed?: string;
		/** What the sub-page is called, at the top of it and on the way back. */
		pageTitle: string;
		/** The row that opens it: "Set each field", "Set each one". */
		editLabel: string;
		/**
		 * The settings on the sub-page, so a deep link to one of them can open the page first.
		 *
		 * Declared rather than inferred. Nothing can read a snippet to find out which rows it will
		 * draw, and a link into a group that cannot be opened is a link that scrolls nowhere.
		 */
		keys?: readonly string[];
		/** The individual rows. Ordinary setting rows; this only decides where they are drawn. */
		children: Snippet;
		disabled?: boolean;
		/**
		 * Rows of the group's own, drawn under the Edit row: the act that goes with the question.
		 *
		 * The Importing groups are each a question about arriving files AND a way to go over the
		 * files already here for the same thing: "Generate now", beside the switches that say what
		 * a new file gets. Kept inside the group rather than as a group of its own beneath it, so
		 * the heading, the switches and the button read as one thing about one stage.
		 */
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

	/* Refused loudly rather than resolved by precedence. A group drawn with two masters has two
	   answers to one question and the reader is shown whichever the markup happened to reach
	   first, which is exactly the kind of thing that is only noticed once somebody's setting
	   stops saving. */
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

	/* Claimed in an effect, not once at setup, and that is not tidiness.
	 *
	 * `keys` is usually derived from settings the caller has not loaded yet (the stash-box rules
	 * arrive from a request), so reading it once at construction claims an EMPTY list and a deep
	 * link to one of those rows finds nothing for ever. The effect re-runs when the list changes,
	 * and its teardown releases the previous claim, which also covers the component going away. */
	$effect(() => drilldown.own(keys, openPage));

	/* `Custom` is appended only while it is the true reading, so it cannot be chosen from a state
	   where it would mean nothing. */
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
