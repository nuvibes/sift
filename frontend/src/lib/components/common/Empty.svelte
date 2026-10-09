<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Empty',
		category: 'composition',
		role: 'what a screen says when it has nothing to show: a glyph, a sentence, maybe an act',
		basis: 'own',
		states: ['a page, with its one act', 'a block inside a page', 'busy']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is nothing to behave. This is a glyph, a sentence and sometimes a
	button in this app's own words; the button is the shared one. */

	/* Nothing here, and what to do about it: a glyph, the screen's own sentence and the one
	   action that ends it. `page` when it is the screen, `block` for the sentence alone. */
	import type { Snippet } from 'svelte';

	import Icon from '$lib/components/Icon.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		/** What is missing, in the screen's own words. A sentence, not a label. */
		children: Snippet;
		/** The glyph above; an entity wall passes its own, so empty walls are told apart. */
		icon?: IconName;
		/** The page's one line in the display face; a block has none. */
		title?: string;
		/** The one control that would end this state. Usually a `Button`. */
		action?: Snippet;
		/** Whether the empty thing is the screen or a block inside one. See the head of the file. */
		scope?: 'page' | 'block';
		/** The older spelling of `scope="block"`; `check_empty_scope.js` refuses it in markup. */
		quiet?: boolean;
		/**
		 * An operation is in flight: a spinner in the glyph's place. Loading content is a Skeleton.
		 */
		busy?: boolean;
	}

	let {
		children,
		icon = 'inbox',
		title,
		action,
		scope,
		quiet = false,
		busy = false
	}: Props = $props();

	/* An unscoped Empty is a page. */
	const block = $derived((scope ?? (quiet ? 'block' : 'page')) === 'block');
</script>

<div class="empty" class:block role="status" aria-busy={busy || undefined}>
	{#if !block}
		<span class="glyph" aria-hidden="true">
			{#if busy}<Spinner size={20} />{:else}<Icon name={icon} size={28} />{/if}
		</span>
	{/if}

	{#if title && !block}<p class="title">{title}</p>{/if}

	<p class="said">
		{#if busy && block}<Spinner size={16} />{/if}
		{@render children()}
	</p>

	{#if action}<div class="act">{@render action()}</div>{/if}
</div>

<style>
	.empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--space-2);
		padding: var(--space-8) var(--space-4);
		text-align: center;
	}

	/* A filled disc, not an outline, which would look like a disabled button. */
	.glyph {
		display: grid;
		place-items: center;
		inline-size: 56px;
		block-size: 56px;
		border-radius: var(--radius-full);
		background: var(--sift-surface-2);
		color: var(--sift-ink-3);
	}

	.title {
		margin: var(--space-1) 0 0;
		font: var(--text-display);
		letter-spacing: var(--tracking-display);
		color: var(--sift-ink);
	}

	/* Inline text, so a link in the sentence wraps with the language. */
	.said {
		margin: 0;
		max-inline-size: 46ch;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* Global: the element is Spinner's. */
	.said :global(.spinner) {
		margin-inline-end: var(--space-2);
	}

	.act {
		margin-block-start: var(--space-2);
	}

	/* Inside something else: one line, where it stands, in the flow of whatever holds it. */
	.block {
		align-items: flex-start;
		padding: 0;
		text-align: start;
	}

	.block .act {
		margin-block-start: var(--space-1);
	}
</style>
