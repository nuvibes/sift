<script lang="ts">
	/*
	 * A swap's receipt, read by its session: the Done screen, and what History opens for the swap.
	 * Undo asks once, then takes every file the swap landed out of the library, and off the disk
	 * where they sit in the swap's own folder.
	 *
	 * NOT ON THE GALLERY: it reads a receipt by its session; the card it draws, SwapReceiptCard, is.
	 */
	import { goto } from '$app/navigation';
	import { ApiError, isMissing } from '$lib/api/client';
	import { ConfirmDialog, Empty, Skeleton } from '$lib/components/common';
	import SwapReceiptCard from './SwapReceiptCard.svelte';
	import { readReceipt, undoQuestion, undoSwap, type SwapReceipt } from './receipt';

	interface Props {
		sessionId: string;
	}

	let { sessionId }: Props = $props();

	/** How long to wait for a receipt the swap's end is still writing, and how often to look. */
	const LOOK_AGAIN_MS = 2000;
	const LOOKS = 3;

	let receipt = $state<SwapReceipt | null>(null);
	let missing = $state(false);
	let failed = $state(false);
	let undoing = $state(false);
	let problem = $state<string | null>(null);
	let asking = $state(false);

	$effect(() => {
		const id = sessionId;
		receipt = null;
		missing = false;
		failed = false;
		problem = null;
		let stopped = false;
		let timer: ReturnType<typeof setTimeout> | undefined;
		const look = async (left: number): Promise<void> => {
			try {
				const read = await readReceipt(id);
				if (!stopped) receipt = read;
			} catch (error) {
				if (stopped) return;
				if (!isMissing(error)) failed = true;
				else if (left > 1) timer = setTimeout(() => void look(left - 1), LOOK_AGAIN_MS);
				else missing = true;
			}
		};
		void look(LOOKS);
		return () => {
			stopped = true;
			if (timer !== undefined) clearTimeout(timer);
		};
	});

	const question = $derived(receipt ? undoQuestion(receipt) : null);
	const undoLabel = $derived(receipt?.two_way ? 'Undo this exchange' : 'Undo this swap');

	async function undo(): Promise<void> {
		if (!receipt || undoing) return;
		undoing = true;
		problem = null;
		try {
			receipt = await undoSwap(receipt.session_id);
		} catch (error) {
			problem =
				error instanceof ApiError
					? (error.detail ?? error.message)
					: "The swap couldn't be undone. Try again.";
		} finally {
			undoing = false;
		}
	}

	function openFolder(): void {
		if (receipt?.folder) void goto(`/browse?in=${encodeURIComponent(receipt.folder.id)}`);
	}
</script>

{#if receipt}
	<SwapReceiptCard
		{receipt}
		{undoing}
		{problem}
		onopen={openFolder}
		onundo={() => (asking = true)}
	/>
	{#if question}
		<ConfirmDialog
			bind:open={asking}
			title={question.title}
			consequence={question.consequence}
			confirmLabel={undoLabel}
			onconfirm={() => void undo()}
		/>
	{/if}
{:else if missing}
	<Empty icon="swap_horiz" scope="block">Sift kept no receipt for this swap.</Empty>
{:else if failed}
	<Empty icon="swap_horiz" scope="block">The receipt couldn't be read. Try again later.</Empty>
{:else}
	<Skeleton lines={4} />
{/if}
