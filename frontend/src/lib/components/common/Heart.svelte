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
	   something is a favourite; this component is told, and reports what was asked for. A toggle
	   holding its own answer would fill the heart on a click the server then refused, and the prop
	   coming back unchanged is exactly the case that cannot correct it. A plain <button> with
	   aria-pressed IS the accessible pattern here: the library would add data attributes and a
	   second source of truth, and no behaviour. */
	/*
	 * The favorite toggle.
	 *
	 * A shortlist, not a rating. It says nothing about quality and it does not touch the stars:
	 * hearting a three-star clip is an ordinary thing to do and is not a contradiction.
	 *
	 * It carries its own pressed state and reports the change; whoever owns the asset decides what
	 * to do about it. That keeps the network call out of here, where a failed request would leave
	 * this component holding a value the server disagrees with.
	 */
	import { onDestroy, untrack } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';

	interface Props {
		favorite: boolean;
		/** Given the value it should become. */
		onchange?: (favorite: boolean) => void;
		size?: 16 | 18 | 20;
		/**
		 * On a dark translucent disc of its own, for a heart drawn over a picture (an entity card's
		 * corner): the scrim token every control over media stands on.
		 */
		grounded?: boolean;
	}

	let { favorite, onchange, size = 18, grounded = false }: Props = $props();

	/*
	 * The small kick when it fills in.
	 *
	 * On the CHANGE rather than on the state, which is the whole of the work here: a rule keyed on
	 * "is a favourite" fires on every already-hearted tile the moment a grid of them is drawn, and a
	 * wall of pulsing hearts on arrival is decoration. This fires when one becomes a favourite, and
	 * never when one stops being one: taking something off a shortlist is not a moment.
	 */
	let popped = $state(false);
	// svelte-ignore state_referenced_locally: the value AT MOUNT is exactly what is wanted: a
	// heart that arrives already filled has not just been filled, and must not kick on arrival.
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
	 * Start the kick, and end it a moment later.
	 *
	 * The timer is deliberately NOT tied to the effect above. Tied to it, the class would stick: an
	 * effect's cleanup runs before the effect runs again, so any later change to what is drawn
	 * would cancel the timer that was going to take the class off, and no other timer would follow,
	 * because by then the heart is already a favourite. A heart left mid-kick is a
	 * heart that stays slightly the wrong size until the screen is rebuilt.
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
		// The tile behind this opens the player when it is clicked. Without this, hearting
		// something also opens it.
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
		/*
		 * The same box as `RatingChip`'s mark, to the pixel: one glyph tall, the same room either
		 * side, the same corner. The two stand in the two corners of every entity card, so they
		 * must match; a rounded square rather than a circle, so the pair agrees whatever is in it.
		 */
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

	/* It grows rather than gaining a ground.
	 *
	 * A colour change alone is refused, and rightly here: a control drawn over a photograph has no
	 * edge of its own, so on a light frame a step in grey is very nearly nothing. A round ground behind it
	 * satisfies the rule and looks wrong: a grey disc under a small glyph reads as a smudge rather
	 * than as the control waking up. So the scale register: it animates, it is visible on any
	 * ground because it is not a colour, and it puts nothing behind a small mark on an image. */
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

	/* Out and back, once, on the spring the design system reserves for exactly this: the small
	   overshoot is what makes it read as a press landing rather than as something growing. `pop` is
	   one of the motions `app.css` names. */
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

	/* A finger's reach on a phone, the glyph keeping its size: at three cards to a phone's line a
	   card is about 112px wide, so the mark cannot grow, and the reach does instead (the ring
	   `Pressable` draws, for the reason given there). */
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
