<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'NarrowBox',
		category: 'control',
		role: 'the small text box that filters the list beside it',
		basis: 'site:<input>; composes:Pressable',
		states: ['empty', 'typed', 'medium']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/*
	 * WHY NOT BITS-UI: a text box is the site's; there is nothing in the library for one to reach.
	 * The box that filters a list as you type, one shape for the facet panel and Select.
	 */
	import type { HTMLInputAttributes } from 'svelte/elements';
	import Icon from '$lib/components/Icon.svelte';
	import Pressable from './Pressable.svelte';

	interface Props extends Omit<HTMLInputAttributes, 'value' | 'type' | 'size'> {
		/** What has been typed. Bindable, so the caller filters on it. */
		value: string;
		/** Names the box for a screen reader: "Filter the People list". */
		label: string;
		/** `small` inside something else, `medium` in a page header beside a Button. */
		size?: 'small' | 'medium';
		/** Where the words start: a field's inset, or the rows' own under the box. */
		inset?: 'field' | 'row';
	}

	/* `class` is merged, not spread, or it would replace `narrow` (caller-class.test.ts). */
	let {
		value = $bindable(''),
		label,
		size = 'small',
		inset = 'field',
		class: extra = '',
		onkeydown,
		...rest
	}: Props = $props();

	let field = $state<HTMLInputElement | null>(null);

	/* The cross empties the element and sends `input`, as a person deleting does, so bound and
	   listening callers both hear it; the caret goes back in. */
	function empty(): void {
		if (!field) return;
		field.value = '';
		field.dispatchEvent(new Event('input', { bubbles: true }));
		field.focus();
	}

	/* Escape empties a box with text and passes through an empty one to the caller. */
	function keydown(event: KeyboardEvent & { currentTarget: EventTarget & HTMLInputElement }): void {
		if (event.key === 'Escape' && event.currentTarget.value !== '') {
			event.preventDefault();
			event.stopPropagation();
			empty();
			return;
		}
		onkeydown?.(event);
	}
</script>

<!-- Every filter box has this cross; the wrapper is position only and takes a caller's class. -->

<div class="narrow-box {extra}">
	<input
		class="narrow"
		class:medium={size === 'medium'}
		class:row-inset={inset === 'row'}
		type="search"
		placeholder="Type to filter"
		aria-label={label}
		autocomplete="off"
		bind:this={field}
		bind:value
		{...rest}
		onkeydown={keydown}
	/>
	<!-- Shown while there is text; Pressable, since a Button would be a large slab. -->
	{#if value}
		<Pressable
			class="field-clear"
			pad="sm"
			feedback="none"
			radius="sm"
			onclick={empty}
			aria-label="Clear the search"
		>
			<Icon name="close" size={16} />
		</Pressable>
	{/if}
</div>

<style>
	/* The strong edge, so it looks like a box before anything is typed. */
	.narrow-box {
		position: relative;
		display: flex;
		align-items: center;
	}

	/* `:where`, so a caller's sizing class always wins. */
	:where(.narrow-box) {
		inline-size: 100%;
		min-inline-size: 0;
	}

	.narrow {
		inline-size: 100%;
		block-size: 28px;
		padding-inline: var(--space-2);
		/* Room for the cross always, so the words do not jump when it appears. */
		padding-inline-end: calc(24px + var(--space-2) * 2);
		border: 1px solid var(--sift-line-strong);
		border-radius: var(--radius-md);
		background: var(--sift-surface-3);
		color: var(--sift-ink);
		font: var(--text-label);
		transition: border-color var(--dur-instant) var(--ease);
	}

	/* A finger's height on a phone. */
	@media (max-width: 767px) {
		.narrow {
			block-size: var(--control-height);
		}
	}

	.narrow:hover {
		border-color: var(--sift-ink-3);
	}

	.narrow::placeholder {
		color: var(--sift-ink-3);
	}

	/* The accent ring, rounded with the box: it is a field being typed into, not a row. */
	.narrow:focus-visible {
		border-color: var(--sift-accent);
	}

	/* The rows' own inset, less the border the rows do not have. */
	.narrow.row-inset {
		padding-inline-start: calc(var(--space-3) - 1px);
	}

	/* A page header's height, so the box lines up with the Add button beside it. */
	.narrow.medium {
		block-size: var(--control-height);
	}

	/* The cross placed over the field, centred on its height; `.field-clear` dresses it. */
	.narrow-box :global(.field-clear) {
		position: absolute;
		inset-inline-end: var(--space-2);
		inset-block: 0;
		margin-block: auto;
	}
</style>
