<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Heart',
		category: 'primitive',
		role: 'the favorite mark, whose state the server owns',
		basis: 'site:<button>',
		states: ['off', 'on', 'pending']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: Toggle owns its pressed state and this one must not. The server owns whether
	something is a favourite; this is told, and a <button> with aria-pressed is the pattern. */
	/*
	 * The favorite toggle: a shortlist, not a rating; it reports the change and the caller writes
	 * it.
	 */
	import { onDestroy, untrack } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';

	interface Props {
		favorite: boolean;
		/** Given the value it should become. */
		onchange?: (favorite: boolean) => void;
		size?: 16 | 18 | 20;
		/** On a dark disc of its own, for a heart drawn over a picture. */
		grounded?: boolean;
	}

	let { favorite, onchange, size = 18, grounded = false }: Props = $props();

	/* The kick when it becomes a favourite, keyed on the change so a drawn wall does not pulse. */
	let popped = $state(false);
	// svelte-ignore state_referenced_locally: the value AT MOUNT is exactly what is wanted: a heart
	// that arrives filled must not kick.
	let was = favorite;
	let settle: ReturnType<typeof setTimeout> | null = null;

	/** A shade longer than the CSS animation, so the class outlasts it and it always finishes. */
	const POP_MS = 260;

	$effect(() => {
		const now = favorite;
		untrack(() => {
			if (now && !was) kick();
			was = now;
		});
	});

	/*
	 * Start the kick and end it; the timer is not tied to the effect, whose cleanup would cancel
	 * it.
	 */
	function kick() {
		if (settle) clearTimeout(settle);
		popped = true;
		settle = setTimeout(() => (popped = false), POP_MS);
	}

	onDestroy(() => {
		if (settle) clearTimeout(settle);
	});

	function toggle(event: MouseEvent) {
		// The tile behind opens the player on a click.
		event.stopPropagation();
		onchange?.(!favorite);
	}
</script>

<button
	type="button"
	class="heart"
	class:on={favorite}
	class:grounded
	class:popped
	onclick={toggle}
	aria-pressed={favorite}
	aria-label={favorite ? 'Remove from favorites' : 'Add to favorites'}
>
	<Icon name="favorite" filled={favorite} {size} />
</button>

<style>
	/* One glyph in a rounded square; the rule on `.heart` says why. */
	.heart {
		display: grid;
		place-items: center;
		/* RatingChip's mark's box to the pixel, as the pair stands in a card's two corners. */
		padding: var(--space-1) var(--space-2);
		border: 0;
		border-radius: var(--radius-md);
		background: transparent;
		color: var(--sift-ink-2);
		cursor: pointer;
		transition:
			transform var(--dur-fast) var(--ease),
			color var(--dur-fast) var(--ease);
	}

	/* It grows rather than gaining a ground, which is visible over any picture. */
	.heart:hover {
		transform: scale(1.18);
		color: var(--sift-heart);
	}

	.heart.on {
		color: var(--sift-heart);
	}

	.heart:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	/* Out and back once, on the `pop` spring. */
	.heart.popped {
		animation: pop var(--dur-base) var(--ease-spring);
	}

	:global(:root[data-motion='reduce']) .heart {
		transition: none;
	}

	/* The colour change already says it. */
	:global(:root[data-motion='reduce']) .heart.popped {
		animation: none;
	}
	/* The disc under a heart over a picture. A ground alone: the shape is the rule above's. */
	.grounded {
		background: var(--sift-scrim);
	}

	/* A finger's reach on a phone through Pressable's ring, the glyph keeping its size. */
	@media (max-width: 767px) {
		:where(.heart) {
			position: relative;
		}

		.heart::after {
			content: '';
			position: absolute;
			inset-block: min(0px, calc((100% - var(--touch-target)) / 2));
			inset-inline: min(0px, calc((100% - var(--touch-target)) / 2));
		}
	}
</style>
