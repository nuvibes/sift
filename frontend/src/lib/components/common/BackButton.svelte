<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'BackButton',
		category: 'primitive',
		role: 'the one way back to the screen before this one, as a link or a button',
		basis: 'site:<a>',
		states: ['link', 'button']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is no back button in it. This is either a link or a button depending on
	   whether there is somewhere named to go, which is a decision about this app's navigation rather
	   than a widget, and both halves are the site's own elements underneath. */

	/*
	 * One step back, drawn the same way everywhere it appears.
	 *
	 * `to` decides what it is. Given a destination it is a link, which can be middle-clicked,
	 * opened in a new tab and read as a link; given none it is a button that goes back, for where
	 * "back" is the only answer (a modal's inner view, a wizard step). A `history.back()` from a
	 * page opened directly would leave the app entirely, which is why a destination is preferred.
	 *
	 * A plain left-click on that link steps back when the previous screen is the place it names.
	 * `/people` is a bare address, and a wall writes which rows it shows into the address it is
	 * left at, so following the fixed href would lose the position; the browser's own step brings
	 * back the page and its scroll. Where the browser cannot say where that screen sits, it goes to
	 * the remembered address. The rule is `returnTo` in `navigation.svelte.ts`, shared with
	 * `Breadcrumbs`. A middle-click, a modifier or arriving here directly still follows the href,
	 * so it never walks somebody out of the app.
	 *
	 * The label is always written out: an arrow alone is ambiguous between the previous screen, the
	 * parent and out of the thing you are inside.
	 */
	interface Props {
		/** Where back IS. Absent makes this a button that returns through history instead. */
		to?: string;
		/** What is behind: "Settings", "People", "All faces". Written out, never just an arrow. */
		label: string;
		/** Called instead of navigating, for a surface that closes rather than moves: a modal view. */
		onback?: () => void;
	}

	let { to, label, onback }: Props = $props();

	import Icon from '$lib/components/Icon.svelte';
	import { returnTo } from '$lib/shell/navigation.svelte';

	function goBack() {
		if (onback) {
			onback();
			return;
		}
		history.back();
	}

	/** A plain click on the link steps back where back is this place. See `returnTo`. */
	function returnInstead(event: MouseEvent) {
		if (to) returnTo(event, to);
	}
</script>

{#if to && !onback}
	<a class="back" href={to} onclick={returnInstead}>
		<Icon name="arrow_back" size={18} />
		{label}
	</a>
{:else}
	<button type="button" class="back" onclick={goBack}>
		<Icon name="arrow_back" size={18} />
		{label}
	</button>
{/if}

<style>
	/*
	 * One rule for both elements, which is the point of writing it here.
	 *
	 * A link and a button must match: a button carries the browser's own font and background
	 * unless told otherwise, and a copy per call site is a copy that forgets to tell it.
	 */
	.back {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
		padding: var(--space-1) var(--space-2);
		margin-inline-start: calc(var(--space-2) * -1);
		border: 0;
		border-radius: var(--radius-md);
		background: transparent;
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
		text-decoration: none;
		cursor: pointer;
		transition:
			color var(--dur-instant) var(--ease),
			background var(--dur-instant) var(--ease);
	}

	.back:hover {
		background: var(--sift-surface-3);
		color: var(--sift-ink);
	}

	.back:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	:global(:root[data-motion='reduce']) .back {
		transition: none;
	}

	/* A finger's height on a phone, where the way back is the press used most. The words keep
	   their size; the press grows around them. */
	@media (max-width: 767px) {
		.back {
			min-block-size: var(--touch-target);
		}
	}
</style>
