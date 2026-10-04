<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'ShellBanner',
		category: 'surface',
		role: 'the quiet line across the top of the app with something to press at the end',
		basis: 'own',
		states: ['info', 'warn', 'with an act']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: a line of text with something to press at the end of it. There is no
	   behaviour here to get wrong (no focus to manage, no keyboard convention to honour) and
	   what it announces comes from the site's own status role. Shape and colour, which is this
	   app's own. */

	/*
	 * The quiet line across the top of the app, above whatever screen is on.
	 *
	 * One sentence and one thing to press. It is the shape Sift uses for a fact somebody should
	 * know and can act on now (a newer Sift exists, this window is a version behind, there is a
	 * measurement on offer, the setup is not finished) and it is deliberately not a toast: a toast
	 * goes away by itself, and none of those should.
	 *
	 * ONE SHAPE, because the notices are the same kind of object, and one of them looking
	 * different would read as one of them being more serious. Identical style blocks in each
	 * notice would be so many chances for that to stop being true, silently, one padding value at
	 * a time.
	 *
	 * `role="status"` rather than `alert`: none of these interrupts anything, and an assertive
	 * announcement over whatever somebody was reading is what a banner about a version number
	 * should never do.
	 */
	import type { Snippet } from 'svelte';

	interface Props {
		/** The sentence. Markup is allowed in it: most of these carry a link. */
		children: Snippet;
		/** What sits at the end of the line. A `Button`, or the one field a notice asks to be filled
		    (the unlock bar's password): nothing here draws its own. */
		action?: Snippet;
		/** For a test that has to find this particular banner among the others. */
		testId?: string;
		/** The act moves under the sentence where the line has no room for both, rather than the
		    sentence being squeezed to a word a line. For an act wider than one press. */
		wraps?: boolean;
	}

	let { children, action, testId, wraps = false }: Props = $props();
</script>

<div class="banner" class:wraps role="status" data-testid={testId}>
	<p>{@render children()}</p>
	{#if action}{@render action()}{/if}
</div>

<style>
	.banner {
		/* The shell's own row. Every one of these is drawn into it, and the row is `auto` so it
		   takes no height at all when none of them renders. */
		grid-area: banner;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
		padding: var(--space-2) var(--space-4);
		background: var(--sift-surface-2);
		border-bottom: 1px solid var(--sift-line);
	}

	p {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	.wraps {
		flex-wrap: wrap;
	}

	/* The sentence keeps its own length and gives up the line only when the act will not fit
	   beside it. */
	.wraps p {
		flex: 1 1 auto;
	}

	/* The link inside somebody's sentence. Global because the sentence is the caller's markup, and
	   bounded by `.banner`, which this file owns and nothing else draws. */
	.banner :global(a) {
		color: var(--sift-ink);
	}
</style>
