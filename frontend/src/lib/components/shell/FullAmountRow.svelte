<script lang="ts">
	/* NOT ON THE GALLERY: it draws the live queue's state and nothing at all while there is none to
	   draw, and the one place it lives is the top of More, which the layout already renders.

	 * The rail's leaf and bolt on a phone, which has no rail: a row at the top of More, the phone's
	 * list of everything the rail does not put on a tab.
	 *
	 * A row rather than the bare glyph, because a phone has no pointer to hover with: the rail says
	 * its sentence in a tooltip, and a tooltip is a thing a finger never sees. Here the sentence is
	 * the row's words and the press is a verb at its end. Drawn in exactly the states the rail draws
	 * the glyph in (`full-amount.ts`), and nothing otherwise.
	 */
	import ActionRow from '$lib/settings-ui/ActionRow.svelte';
	import { session } from '$lib/shell/session.svelte';
	import {
		currentFullAmount,
		currentShare,
		FULL_AMOUNT_COPY,
		fullAmountSays,
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
	<div class="full-amount-row">
		<ActionRow
			icon={shown === 'full' ? 'bolt' : 'energy_savings_leaf'}
			label={fullAmountSays(shown, share)}
			action={shown === 'full' ? FULL_AMOUNT_COPY.stepBack : FULL_AMOUNT_COPY.useFull}
			busy={pressing}
			onclick={() => void press()}
		/>
	</div>
{/if}

<style>
	/* The press's glyph in Sift's green, as on the rail; the words keep their own ink. */
	.full-amount-row > :global(.row > .control > .press > .btn > .icon) {
		color: var(--sift-ok);
	}
</style>
