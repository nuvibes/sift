<script lang="ts">
	/* NOT ON THE GALLERY: it draws only the live queue's state, at the top of More.

	 * The rail's leaf and bolt on a phone, which has no rail: a row at the top of More, the phone's
	 * list of everything the rail does not put on a tab.
	 *
	 * A row rather than the bare glyph, because a finger never sees a tooltip. Drawn in exactly the
	 * states the rail draws the glyph in (`full-amount.ts`), dimmed as the rail dims it.
	 */
	import ActionRow from '$lib/settings-ui/ActionRow.svelte';
	import { session } from '$lib/shell/session.svelte';
	import {
		currentFullAmount,
		currentShare,
		FULL_AMOUNT_COPY,
		fullAmountSays,
		leafDimmed,
		pressFullAmount
	} from './full-amount';

	const shown = $derived(currentFullAmount(session.isAdmin));
	const share = $derived(currentShare());
	let pressing = $state(false);

	async function press(): Promise<void> {
		pressing = true;
		try {
			await pressFullAmount(shown !== 'full');
		} finally {
			pressing = false;
		}
	}
</script>

{#if shown !== null}
	<div class="full-amount-row" class:full={shown === 'full'} class:dimmed={leafDimmed(shown)}>
		<ActionRow
			icon={shown === 'full' ? 'bolt_boost' : 'energy_savings_leaf'}
			label={fullAmountSays(shown, share)}
			action={shown === 'full' ? FULL_AMOUNT_COPY.stepBack : FULL_AMOUNT_COPY.useFull}
			busy={pressing}
			onclick={() => void press()}
		/>
	</div>
{/if}

<style>
	/* The press's glyph as on the rail, the leaf green and the bolt yellow; the words keep their ink. */
	.full-amount-row > :global(.row > .control > .press > .btn > .icon) {
		color: var(--sift-ok);
	}

	.full-amount-row.full > :global(.row > .control > .press > .btn > .icon) {
		color: var(--sift-warn);
	}

	.full-amount-row.dimmed > :global(.row > .control > .press > .btn > .icon) {
		opacity: var(--disabled-opacity);
	}
</style>
