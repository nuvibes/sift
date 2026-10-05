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
	   button: shape and words, which are this app's own. The button inside it IS the shared one. */

	/*
	 * Nothing here, and what to do about it.
	 *
	 * ## Why this exists
	 *
	 * A screen writing its own empty state tends to write a bare `<p class="empty">` in the quiet
	 * ink, the version that says the least: it reads as a screen that failed rather than as a screen
	 * with nothing in it yet, and it never says what would put something there.
	 *
	 * Copies also disagree ("Nothing here yet", "No links yet", "Nobody yet", "Loading..." in the
	 * same slot), so the same grey line would mean "empty", "still working" and "broken" depending
	 * on which screen you were on.
	 *
	 * ## The three parts, and why the third one matters most
	 *
	 * The glyph says at a glance that this is a state and not a failure. The sentence says what is
	 * missing, in the screen's own words: "nothing is attributed to them yet" and "you have not
	 * favourited anything" are different facts and only the screen knows which it is.
	 *
	 * The `action` is the part that is usually left out and is the reason somebody is stuck. A screen
	 * that says a list is empty and does not say what fills it is a dead end; the one control that
	 * would end it belongs right there, not somewhere else on the page.
	 *
	 * ## Two scopes, and the caller says which
	 *
	 * `page`: the empty thing IS the screen (a wall, a tab, a list that fills the page). The glyph,
	 * one line in the display face, one sentence, and the one action. `block`: a list or a band
	 * inside a page that is otherwise full, where the whole panel would be a large announcement
	 * about a small absence, so it is the sentence alone, where it stands. The words are the same in
	 * both, so a screen never writes a second, shorter version of them.
	 */
	import type { Snippet } from 'svelte';

	import Icon from '$lib/components/Icon.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		/** What is missing, in the screen's own words. A sentence, not a label. */
		children: Snippet;
		/**
		 * The glyph above it.
		 *
		 * Defaults to the empty tray. An entity's wall passes its OWN glyph, the rail's for that
		 * entity (`EntityGrid`): five walls under one tray read as one shared empty screen rather
		 * than as an empty People or Tags wall. That the screen's icon only repeats "this is the
		 * People page" holds for other screens and not for the walls, which differ by nothing else.
		 */
		icon?: IconName;
		/**
		 * The one line in the display face, on a page: 'No files match "beach".'. A block draws no
		 * heading; its sentence is the whole of it.
		 */
		title?: string;
		/** The one control that would end this state. Usually a `Button`. */
		action?: Snippet;
		/** Whether the empty thing is the screen or a block inside one. See the head of the file. */
		scope?: 'page' | 'block';
		/**
		 * The older spelling of `scope="block"`, read as exactly that. `check_empty_scope.js`
		 * refuses it in markup, as it refuses an `Empty` naming no scope.
		 */
		quiet?: boolean;
		/**
		 * Something is in flight and the sentence says what: "Asking each stash-box about this file",
		 * "Working out what this would do". A spinner takes the glyph's place and the region says it
		 * is busy.
		 *
		 * For an OPERATION, not for content on its way: a list that is loading is a `Skeleton`
		 * (skeletons shimmer, never spinners, for anything that will become content). Here so no
		 * screen writes `<p class="waiting"><Spinner /> ...` with a flex rule of its own.
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

	/* The scope asked for, the older flag read as a block, and nothing said read as a page: the
	   full panel is what an unscoped `Empty` draws. */
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

	/*
	 * A circle of the recessed ground, not an outline.
	 *
	 * An outlined glyph in the middle of an empty screen reads as a button somebody has disabled:
	 * it has an edge, so it looks pressable. A filled disc has no edge to promise anything.
	 */
	.glyph {
		display: grid;
		place-items: center;
		inline-size: 56px;
		block-size: 56px;
		border-radius: var(--radius-full);
		background: var(--sift-surface-2);
		color: var(--sift-ink-3);
	}

	/* The one line in the display face that says what is missing, above the sentence that says
	   what to do about it. */
	.title {
		margin: var(--space-1) 0 0;
		font: var(--text-display);
		letter-spacing: var(--tracking-display);
		color: var(--sift-ink);
	}

	/*
	 * The sentence is inline content, laid out as text.
	 *
	 * A flex container blockifies every child, so a link inside the sentence (as on
	 * `/organize/tagger`: "... matching has to be switched on first, in
	 * <SettingLink>Settings</SettingLink>.") would become separate boxes wrapping where the row
	 * ends rather than where the language does. The flex belongs to `.empty` around the words. A
	 * `Spinner` is an inline-block that sits on the text's baseline, so a margin gives it its gap.
	 * Centring is inherited through `text-align` from the box above, where a page centres and a
	 * block starts.
	 */
	.said {
		margin: 0;
		max-inline-size: 46ch;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* The room beside the words, given to the one thing that stands there. `:global`
	   because the element is `Spinner`'s own, compiled in its file. */
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
