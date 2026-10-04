<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'SharingMark',
		category: 'primitive',
		role: 'the mark that says who else can see this',
		basis: 'own',
		states: ['private', 'shared', 'public']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: an icon with a label. There is no behaviour in it and bits-ui has no primitive for one. */
	/*
	 * The badge that says something has been said about this.
	 *
	 * The same two glyphs the grid draws on a tile, for the walls that list things rather than
	 * files: a collection, a person, a tag, a site. One component so the two cannot drift: the
	 * moment a person's card and a file's tile use different marks for the same fact, the marks stop
	 * being readable at a glance, which is the only thing they are for.
	 *
	 * Nothing at all when nothing has been said, which is most of a library. Restricted wins when
	 * both are somehow set, exactly as it does in the resolver.
	 *
	 * It is a BUTTON wherever it is given somewhere to go, because its own tooltip ends by saying so:
	 * every mark reads "(click to see sharing menu)", and a mark that says so over a plain span is
	 * a promise nothing keeps. A mark with no `onclick`
	 * is still drawn, as text, so the one place that has nothing to open does not promise anything.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { HIDDEN_REST, HIDDEN_VERB, HIDDEN_WORDS, markFor } from '$lib/library/sharing-marks';

	interface Props {
		shared?: boolean;
		restricted?: boolean;
		/** Where it was decided, for the things that have something above them: a folder, and a Site
		 *  under a network. Left out (not false) for a thing that inherits nothing (a tag, a
		 *  person, a collection), whose mark is then always the solid "decided here" one. */
		shared_here?: boolean;
		restricted_here?: boolean;
		/** True only where the thing being marked is a FILE, which always says where: a file handed
		 *  no `shared_here` was decided above it. */
		file?: boolean;
		/**
		 * In the vault, and being listed anyway, which only happens with the vault open.
		 *
		 * Its own glyph beside the sharing one, never folded into it: sharing is about WHO, and this
		 * is about nobody. Without it, hiding something while the vault is open would change
		 * nothing you could see: the row stays, correctly, and would look untouched.
		 */
		hidden?: boolean;
		/** Open the sharing panel on whatever this mark is about. Absent draws it as plain text. */
		onopen?: () => void;
		/*
		 * Open the Hidden panel, from the crossed-out eye.
		 *
		 * Its own callback, because the two glyphs answer two questions: a share is about who may;
		 * hiding is about what you want on your own screen, and no share overrides it. One glyph
		 * each, one panel each. Absent leaves the eye as plain text, as `onopen` does for the
		 * sharing mark, so a surface with nowhere to open a panel draws neither as a button.
		 */
		onhidden?: () => void;
		/** Kept out of swaps by its own "Don't enrich" (`local`) or "Don't swap": the menus' own
		 *  "Don't swap" glyph in the danger ink, beside the others. A folder's reaches every file in it. */
		refused?: 'local' | 'swap';
	}

	let {
		shared = false,
		restricted = false,
		// Undefined rather than false, and that is the whole of what tells a thing that inherits
		// nothing apart from one whose share came from above. See `markFor`.
		shared_here = undefined,
		restricted_here = undefined,
		file = false,
		hidden = false,
		onopen,
		onhidden,
		refused
	}: Props = $props();

	const mark = $derived(markFor({ shared, restricted, shared_here, restricted_here }, { file }));

	/*
	 * The press stops here.
	 *
	 * Every wall this sits on has something of its own listening: a card is a link, a chip selects
	 * the tag, a folder row selects the folder. Without this, opening the panel also navigates or
	 * changes what is selected underneath it, and the panel comes up over a screen that moved.
	 */
	function press(event: MouseEvent) {
		event.preventDefault();
		event.stopPropagation();
		onopen?.();
	}

	/** The same swallowing, for the other glyph. See `press`. */
	function pressHidden(event: MouseEvent) {
		event.preventDefault();
		event.stopPropagation();
		onhidden?.();
	}
</script>

{#if mark || hidden || refused}
	<span class="marks">
		{#if mark}
			<Tooltip
				lead={mark.verb}
				tone={mark.kind === 'restricted' ? 'restrict' : mark.kind === 'both' ? 'both' : 'share'}
				label={mark.rest}
				placement="top"
			>
				{#if onopen}
					<button
						type="button"
						class="mark"
						class:restricted={mark.kind === 'restricted'}
						class:both={mark.kind === 'both'}
						aria-label="{mark.words}. Open sharing"
						onclick={press}
					>
						<Icon name={mark.icon} size={16} filled={mark.filled} />
					</button>
				{:else}
					<span
						class="mark"
						class:restricted={mark.kind === 'restricted'}
						class:both={mark.kind === 'both'}
					>
						<Icon name={mark.icon} size={16} filled={mark.filled} label={mark.words} />
					</span>
				{/if}
			</Tooltip>
		{/if}

		{#if hidden}
			<Tooltip lead={HIDDEN_VERB} tone="hidden" label={HIDDEN_REST} placement="top">
				{#if onhidden}
					<button
						type="button"
						class="mark vaulted"
						aria-label="{HIDDEN_WORDS}. Open hidden"
						onclick={pressHidden}
					>
						<Icon name="visibility_off" size={16} filled />
					</button>
				{:else}
					<span class="mark vaulted">
						<Icon name="visibility_off" size={16} filled label={HIDDEN_WORDS} />
					</span>
				{/if}
			</Tooltip>
		{/if}

		{#if refused}
			<Tooltip
				lead={refused === 'local' ? 'Kept local' : 'Kept out of swaps'}
				label={refused === 'local'
					? '\u2014 nothing inside is sent to a stash-box or offered in a swap'
					: '\u2014 nothing inside is offered in a swap'}
				placement="top"
			>
				<span class="mark refused">
					<Icon
						name="do_not_disturb_on"
						size={16}
						filled
						label={refused === 'local' ? 'Kept local' : 'Kept out of swaps'}
					/>
				</span>
			</Tooltip>
		{/if}
	</span>
{/if}

<style>
	/* The same colours the words wear in the sharing panel, so one state has one colour wherever it
	   is drawn: a card, a chip, a folder row, a tile. */

	/* Two facts, side by side, never stacked: a thing can be shared AND hidden, and one glyph on top
	   of another says neither. */
	.marks {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
		flex: none;
	}

	/* Hidden is not an alarm: it is the ordinary state of everything behind the PIN, so it keeps
	   the plain ink rather than borrowing one of the three sharing colours. */
	.mark.vaulted {
		color: var(--sift-ink-2);
	}

	.mark {
		display: inline-flex;
		align-items: center;
		flex: none;
		color: var(--sift-ok);
	}

	/* A button, but not drawn as one: it is a fact in a row of facts, and a bordered box around it
	   would read as a control that does something to the thing rather than as a badge that explains
	   it. The pointer and the focus ring are what say it can be pressed. */
	button.mark {
		padding: 0;
		border: 0;
		background: none;
		cursor: pointer;
		border-radius: var(--radius-sm);
	}

	button.mark:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	/* A 16px badge, and a finger's reach around it on a phone: the ring `Pressable` draws, for the
	   reason given there (the badge keeps its size in the row of facts). */
	@media (max-width: 767px) {
		button.mark {
			position: relative;
		}

		button.mark::after {
			content: '';
			position: absolute;
			inset-block: min(0px, calc((100% - var(--touch-target)) / 2));
			inset-inline: min(0px, calc((100% - var(--touch-target)) / 2));
		}
	}

	.mark.restricted,
	.mark.refused {
		color: var(--sift-bad-text);
	}

	.mark.both {
		color: var(--sift-warn);
	}
</style>
