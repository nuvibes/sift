<script lang="ts">
	/* NOT ON THE GALLERY: it reads one person's held faces from the server and draws nothing
	   without them; the row it draws is the person page's own "needs your input" row shape. */
	/*
	 * The face descriptions a swap brought for somebody this library already had, offered on their
	 * page.
	 *
	 * Another install's say-so about a person we know is a suggestion, never a reference trusted to
	 * name faces, so the swap holds them and nothing uses them until an admin presses Add here. The
	 * row has the shape of the one above it (`WaitingForYou`'s "need your input"): what is waiting
	 * on the left, the act on the right, one line under it saying what the press does.
	 *
	 * Draws nothing when nothing is waiting, which is the ordinary case, and for anybody but an
	 * admin: the routes are an admin's, like every swap route.
	 */
	import { Button } from '$lib/components/common';
	import { addHeldFaces, heldFaces } from '$lib/components/swap/swap';
	import { primedOr, readingAbout } from '$lib/entity/subject.svelte';
	import { counted } from '$lib/entity/entity-counts';
	import { session } from '$lib/shell/session.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';

	interface Props {
		personId: string;
		/** Whose they are, said in the line under the row and in the toast. */
		name: string;
		/** Told how many were added, so what reads the references can read them again. */
		onadded?: (added: number) => void;
	}

	let { personId, name, onadded }: Props = $props();

	let asked = $state(0);
	let adding = $state(false);

	const held = readingAbout<number>(
		() => personId,
		async (id) => (session.isAdmin ? (await primedOr('held', id, () => heldFaces(id))).waiting : 0),
		0,
		() => asked
	);

	const waiting = $derived(held.value);
	const first = $derived(name.trim().split(/\s+/)[0] || 'them');
	const said = $derived(
		waiting === 1 ? '1 facial fingerprint' : `${counted(waiting)} facial fingerprints`
	);

	async function add(): Promise<void> {
		adding = true;
		try {
			const answer = await addHeldFaces(personId);
			const added =
				answer.added === 1
					? '1 facial fingerprint'
					: `${counted(answer.added)} facial fingerprints`;
			toasts.show([`Added ${added} to `, thing('person', personId, name)]);
			onadded?.(answer.added);
		} catch {
			toasts.show("Those couldn't be added. Try again in a moment.", { tone: 'error' });
		} finally {
			adding = false;
			asked += 1;
		}
	}
</script>

{#if waiting > 0}
	<div class="asks">
		<div class="row">
			<span class="number">{said} from a swap</span>
			<Button
				size="small"
				tone="secondary"
				icon="add"
				busy={adding}
				aria-label="Add what a swap brought: {said}"
				onclick={() => void add()}>Add</Button
			>
		</div>
		<p class="help">
			Another Sift sent these with {first}'s files. Add these faces, and Sift uses them to recognize {first}.
		</p>
	</div>
{/if}

<style>
	/* The row, then the line saying what the press does, close under it: `WaitingForYou`'s shape. */
	.asks {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	.row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2);
	}

	.number {
		font: var(--text-body);
		font-variant-numeric: tabular-nums;
		color: var(--sift-ink);
	}

	.help {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}
</style>
