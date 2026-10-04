<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'KeyEcho',
		category: 'composition',
		role: 'the state a key just changed, shown for a moment over the picture it changed',
		basis: 'own',
		states: ['lit', 'unlit']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is no behaviour to borrow. This is a glyph, a moment and a fade. The
	   library's closest thing is a toast, which is a message about something that happened
	   elsewhere: this is a readout of what is on screen right now, drawn on the thing it is about. */

	/*
	 * What a key just did, said on the picture rather than nowhere.
	 *
	 * R cycles the repeat setting and L walks a loop's two marks. Both are single keys with no
	 * control under the hand, and what moves is a button inside a shut drawer, so the press would
	 * read as nothing happening. This shows the new state over the content for about a second.
	 *
	 * It echoes a press. The press is counted and handed in, and a change of state with no press
	 * behind it shows nothing: using the button in the drawer needs no badge, since that control is
	 * under the pointer and lit. A press that changes nothing still echoes, because it says what
	 * the setting is. The count it is drawn with is never echoed, so a player opening on "play
	 * through" shows no badge.
	 *
	 * The hold is not scaled by the motion setting; the fade is, through `arrive`. How long the
	 * badge stays is not motion, and scaling it like a duration would collapse the echo to a single
	 * frame.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import type { IconName } from '$lib/design/icons';
	import { arrive } from '$lib/shell/motion.svelte';

	interface Props {
		/** The glyph for the state that is true now. */
		icon: IconName;
		/** The same state in words, for a screen reader and for the gallery. */
		label: string;
		/**
		 * Whether the state is the OFF one, drawn dimmer.
		 *
		 * Two of the three repeat answers share a glyph: "stop at the end" is the repeat arrows
		 * not lit, which is the rule the control in the drawer already follows, so without this
		 * the badge would say the same thing for two different answers.
		 */
		muted?: boolean;
		/**
		 * The number or the name behind the state: "+2 (54)", "2x", the file that just arrived.
		 *
		 * A glyph alone suffices for a setting with two or three answers, but says nothing for keys
		 * that move a number: a speaker icon after the volume key says only that the volume
		 * changed. Both halves are given, what the press did and where it left the level.
		 *
		 * Absent is a badge that is a glyph and nothing else, as for the three repeat answers and
		 * the two mutes, whose words are the state.
		 */
		detail?: string;
		/**
		 * How many times the key this badge echoes has been pressed.
		 *
		 * A COUNT rather than a flag, because the answer to two presses in a row is two badges and a
		 * flag cannot say that, and because the second of two presses can leave the setting exactly
		 * where the first did, which a state this component watched itself could never tell from
		 * nothing happening at all.
		 *
		 * Whoever owns the key raises it. Nothing else may: a control changing the same setting is
		 * precisely the case this is here to stay quiet for. Zero is a badge that has not been
		 * pressed, which is what every one of these is drawn as.
		 */
		press?: number;
	}

	let { icon, label, muted = false, detail, press = 0 }: Props = $props();

	/** How long it stays before it starts going. About a second, with the fades either side of it. */
	const HOLD_MS = 900;

	let showing = $state(false);

	/* Plain variables rather than `$state`, for the reason `appears` in `$lib/shell/motion` gives: the
	   effect below both writes and reads them, and reactive state read by the body that wrote it
	   schedules that body again: a guard that runs twice, silently. */
	let seen: number | null = null;
	let timer: ReturnType<typeof setTimeout> | undefined;

	$effect(() => {
		const now = press;
		/* The count it was drawn with, whatever it happens to be. Nothing has been pressed while this
		   badge has been on screen, and a caller that mounts one mid-sitting is not owed a badge for
		   presses that happened before it existed. */
		if (seen === null) {
			seen = now;
			return;
		}
		if (now === seen) return;
		seen = now;
		showing = true;
		clearTimeout(timer);
		timer = setTimeout(() => (showing = false), HOLD_MS);
	});

	// A pending timer outlives the component otherwise: step to the next file a moment after
	// pressing R and it fires against something that is no longer there.
	$effect(() => () => clearTimeout(timer));
</script>

{#if showing}
	<!-- `role="status"` and the words as its name: somebody who cannot see the badge pressed the
	     same key and is owed the same answer. The glyph carries no label of its own, or the state
	     would be announced twice. -->
	<div
		class="key-echo"
		class:unlit={muted}
		class:worded={detail !== undefined}
		role="status"
		aria-label={detail === undefined ? label : `${label} ${detail}`}
		transition:arrive={{ pace: 'fast' }}
	>
		<Icon name={icon} size={20} label="" />
		{#if detail !== undefined}
			<!-- `aria-hidden`, because the name on the badge already carries these words: a status
			     region announcing its label AND its contents says the number twice. -->
			<span class="detail" aria-hidden="true">{detail}</span>
		{/if}
	</div>
{/if}

<style>
	/*
	 * A glass badge over the content, on the scrim every control that sits over media uses.
	 *
	 * It does not place itself. Where the corner of the picture is is a fact about the frame, and
	 * the two callers (the player's stage and a Theater cell) have different frames; a component
	 * that pinned itself to a corner would be right in one of them and wrong in the other.
	 */
	.key-echo {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: var(--space-2);
		inline-size: var(--space-8);
		block-size: var(--space-8);
		border-radius: var(--radius-md);
		background: var(--sift-scrim-strong);
		color: var(--sift-ink);
		/* It reports; it is never reached for. A badge that swallowed a pointer would be a hole in
		   the corner of the picture for the second it is up. */
		pointer-events: none;
	}

	/*
	 * WITH A DETAIL it is a pill rather than a square, and it grows with what it says.
	 *
	 * The square is right for a glyph on its own and wrong the moment there are words beside it: a
	 * fixed width either cuts "+10 (100)" off or leaves a hole around "2x". `inline-size: auto` with
	 * the square's own measurement as a floor keeps the two kinds of badge the same height and the
	 * same shape at their smallest, which is what makes them read as one thing.
	 */
	.key-echo.worded {
		inline-size: auto;
		min-inline-size: var(--space-8);
		padding-inline: var(--space-3);
	}

	/* The numbers in the app's tabular face, so a volume counting up does not jitter under the eye:
	   the same rule every other number in Sift is drawn by. */
	.detail {
		font: var(--text-label);
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}

	/* The off answer, in the quiet ink: the same distinction the control in the drawer draws by
	   not being lit. */
	.key-echo.unlit {
		color: var(--sift-ink-3);
	}
</style>
