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
	/* The badge saying something has been said about this, the same glyphs as a file's tile; a button
	 * only where there is somewhere to open, as its tooltip promises. Restricted wins over shared.
	 * */
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { HIDDEN_REST, HIDDEN_VERB, HIDDEN_WORDS, markFor } from '$lib/library/sharing-marks';

	interface Props {
		shared?: boolean;
		restricted?: boolean;
		/** Where it was decided; left out for a thing that inherits nothing. */
		shared_here?: boolean;
		restricted_here?: boolean;
		/** Only on a file, which always says where. */
		file?: boolean;
		/** In the vault and listed anyway: its own glyph, as hiding is about nobody. */
		hidden?: boolean;
		/** Open the sharing panel on whatever this mark is about. Absent draws it as plain text. */
		onopen?: () => void;
		/** Open the Hidden panel from the crossed-out eye; absent, plain text. */
		onhidden?: () => void;
		/** Kept out of swaps: the "Don't swap" glyph in danger ink. */
		refused?: 'local' | 'swap';
	}

	let {
		shared = false,
		restricted = false,
		// Undefined, not false: that tells an inheritless thing apart (`markFor`).
		shared_here = undefined,
		restricted_here = undefined,
		file = false,
		hidden = false,
		onopen,
		onhidden,
		refused
	}: Props = $props();

	const mark = $derived(markFor({ shared, restricted, shared_here, restricted_here }, { file }));

	/* The press stops here, so the wall under it does not move. */
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
	/* The sharing panel's colours. */

	/* Side by side, never stacked. */
	.marks {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
		flex: none;
	}

	/* Hidden in the plain ink, not an alarm. */
	.mark.vaulted {
		color: var(--sift-ink-2);
	}

	.mark {
		display: inline-flex;
		align-items: center;
		flex: none;
		color: var(--sift-ok);
	}

	/* A button not drawn as one: a fact in a row of facts. */
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

	/* A finger's reach on a phone through Pressable's ring. */
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
