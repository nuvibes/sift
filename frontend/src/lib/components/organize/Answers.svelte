<script lang="ts" module>
	import type { Snippet } from 'svelte';
	import type { IconName } from '$lib/design/icons';

	/** One answer to a question on Organize: its words, and what pressing it does. */
	export interface Answer {
		label: string;
		/** What pressing it does. A press that goes somewhere is a `run` that navigates. */
		run: () => void;
		icon?: IconName;
		/** Draw the glyph solid, as the verb it came from does. */
		filled?: boolean;
		/** Red in the menu: the answer destroys something. */
		destructive?: boolean;
		disabled?: boolean;
	}

	/** An affirmative that opens a chooser first ("Add as person"). */
	export interface Door {
		label: string;
		icon?: IconName;
		disabled?: boolean;
		/** What the button opens: a picker, or rows. */
		menu: Snippet;
		/** What the opened menu is called, for anybody who cannot see it. */
		menuLabel: string;
		/** Whether the menu scrolls its own contents: rows do, a picker that scrolls itself does not. */
		scrolls?: boolean;
	}

	/** What the affirmative says while it is on its way, the same on every shape. */
	export const ANSWERING = 'Answering\u2026';
</script>

<script lang="ts">
	/* The answers to one Organize question: the affirmative is the button, the rest behind its
	 * chevron, at the end of the row and the foot of what holds it. */
	import {
		Button,
		ContextMenuGroup,
		ContextMenuItem,
		MenuButton,
		SplitButton
	} from '$lib/components/common';

	interface Props {
		/** The affirmative: the answer the question expects. */
		yes: Answer | Door;
		/** Every other answer, in the order the question reads them. */
		rest?: readonly Answer[];
		/** Who or what the question is about, for the chevron's name: "More for Duplicates". */
		about: string;
		/** The affirmative is on its way: its words say so and nothing can be pressed. */
		busy?: boolean;
		/** Nothing here can be pressed (another answer on the screen is on its way). */
		disabled?: boolean;
	}

	let { yes, rest = [], about, busy = false, disabled = false }: Props = $props();

	/* The two kinds of affirmative, told apart once so each branch below reads its own. */
	const door = $derived('menu' in yes ? yes : null);
	const act = $derived('menu' in yes ? null : yes);
</script>

<!-- The other answers in parts: decided here, then those that go somewhere, then destructive. -->
{#snippet others()}
	{@const destroys = rest.filter((one) => one.destructive)}
	{@const goes = rest.filter((one) => !one.destructive && one.icon === 'arrow_forward')}
	{@const here = rest.filter((one) => !one.destructive && one.icon !== 'arrow_forward')}
	{#each [here, goes, destroys] as part, at (at)}
		{#if part.length > 0}
			<ContextMenuGroup>
				{#each part as one (one.label)}
					<ContextMenuItem
						label={one.label}
						icon={one.icon}
						filled={one.filled}
						destructive={one.destructive}
						disabled={disabled || one.disabled}
						onselect={one.run}
					/>
				{/each}
			</ContextMenuGroup>
		{/if}
	{/each}
{/snippet}

<div class="answers">
	{#if door && rest.length === 0}
		<MenuButton
			label={door.menuLabel}
			disabled={disabled || busy || door.disabled}
			scrolls={door.scrolls ?? true}
		>
			{#snippet trigger({ props })}
				<Button {...props} tone="primary" size="small" icon={door.icon} type="button"
					>{busy ? ANSWERING : door.label}</Button
				>
			{/snippet}
			{@render door.menu()}
		</MenuButton>
	{:else if door}
		<SplitButton
			tone="primary"
			size="small"
			icon={door.icon}
			disabled={disabled || busy || door.disabled}
			trailingLabel={`More for ${about}`}
			leadMenu={door.menu}
			leadMenuLabel={door.menuLabel}
			leadMenuScrolls={door.scrolls ?? true}
			menu={others}
		>
			{busy ? ANSWERING : door.label}
		</SplitButton>
	{:else if act && rest.length === 0}
		<Button
			tone="primary"
			size="small"
			icon={act.icon}
			{busy}
			disabled={disabled || act.disabled}
			onclick={act.run}>{act.label}</Button
		>
	{:else if act}
		<SplitButton
			tone="primary"
			size="small"
			icon={act.icon}
			disabled={disabled || busy || act.disabled}
			trailingLabel={`More for ${about}`}
			onclick={act.run}
			menu={others}
		>
			{busy ? ANSWERING : act.label}
		</SplitButton>
	{/if}
</div>

<style>
	/*
	 * At the row's end and the foot of its holder; inside a card, `--card-actions-justify` decides.
	 */
	.answers {
		display: flex;
		justify-content: var(--card-actions-justify, flex-end);
		align-items: center;
		margin-block-start: auto;
		margin-inline-start: var(--card-actions-push, auto);
		min-inline-size: 0;
	}
</style>
