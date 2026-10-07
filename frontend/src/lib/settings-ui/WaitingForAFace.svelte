<script lang="ts">
	/* Settings > Faces: the people a facial fingerprints file or a folder brought whom no face in
	 * the library matches yet. Listed so they can be seen at all, each with how many faces, the file
	 * or folder and when, and the two answers a person can give now: Create a person, or Remove.
	 *
	 * Shaped as People Sift can recognize is, just above it: the same box filtering the list as
	 * you type, the count, and the list in a scroll of its own capped at that list's height, so a
	 * file of five hundred people is one scroll among the rows rather than the pane growing by all
	 * of them. The whole list is read in one go (the route holds no pages), so the box narrows what
	 * is held rather than asking again.
	 */
	import { goto } from '$app/navigation';
	import { Button, ConfirmDialog, Empty, NarrowBox, Scroller } from '$lib/components/common';
	import { ApiError } from '$lib/api/client';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { onRecord } from '$lib/shell/when';
	import {
		makePersonFromFingerprints,
		removeWaitingFingerprints,
		waitingFingerprints,
		type WaitingFingerprints
	} from '$lib/people/fingerprint-offers';
	import SettingGroup from './SettingGroup.svelte';
	import { COPY } from './Faces.search';

	interface Props {
		/** Recognition is on: creating a person needs it, removing does not. */
		enabled: boolean;
		/** Bumped by the pane after an import, so the list is read again. */
		read: number;
		/** A person was created, so the pane's list of who Sift can recognize changed. */
		onchanged: () => void;
		/** Each answer as it is read, or null when it could not be: the export row counts it. */
		onread?: (answer: WaitingFingerprints | null) => void;
	}

	let { enabled, read, onchanged, onread }: Props = $props();

	let held = $state<WaitingFingerprints['items']>([]);
	let lookingFor = $state('');
	/* What the box leaves, by any part of the name and any case, as the list above narrows. */
	const shown = $derived.by(() => {
		const needle = lookingFor.trim().toLowerCase();
		return needle ? held.filter((one) => one.name.toLowerCase().includes(needle)) : held;
	});
	let busy = $state<string | null>(null);
	let removing = $state<{ id: string; name: string } | null>(null);
	let removeOpen = $state(false);

	async function readHeld() {
		try {
			const answer = await waitingFingerprints();
			held = answer.items;
			onread?.(answer);
		} catch {
			held = [];
			onread?.(null);
		}
	}

	$effect(() => {
		void read;
		void readHeld();
	});

	async function create(entryId: string, name: string) {
		if (busy) return;
		busy = entryId;
		try {
			const person = await makePersonFromFingerprints(entryId);
			toasts.show(COPY.waiting.created(name), {
				tone: 'success',
				action: { label: COPY.waiting.open, run: () => void goto(`/people/${person}`) }
			});
			await readHeld();
			onchanged();
		} catch (error) {
			toasts.show((error instanceof ApiError && error.detail) || COPY.waiting.couldNotCreate, {
				tone: 'error'
			});
		} finally {
			busy = null;
		}
	}

	async function remove(entryId: string) {
		busy = entryId;
		try {
			await removeWaitingFingerprints(entryId);
			await readHeld();
		} catch {
			toasts.show(COPY.waiting.couldNotRemove, { tone: 'error' });
		} finally {
			busy = null;
		}
	}
</script>

<SettingGroup id="faces.waiting" heading={COPY.lists.waiting.name} help={COPY.lists.waiting.help}>
	{#if held.length === 0}
		<Empty scope="block">{COPY.waiting.none}</Empty>
	{:else}
		<div class="stack">
			<NarrowBox label={COPY.waiting.search} bind:value={lookingFor} />
			{#if shown.length === 0}
				<Empty scope="block">{COPY.waiting.noMatch(lookingFor.trim())}</Empty>
			{:else}
				<p class="count">{COPY.waiting.count(shown.length)}</p>
				<div class="held-box">
					<Scroller>
						<ul class="held">
							{#each shown as entry (entry.entry_id)}
								<li>
									<span class="entry">
										<span class="who">{entry.name}</span>
										<span class="line">
											{COPY.waiting.faces(entry.faces, entry.confirmed)} &middot; {COPY.waiting.from(
												entry.source,
												onRecord(entry.added_at / 1000, { inline: true })
											)}
										</span>
									</span>
									<!-- Remove first and Create a person last, at the row's end at every width:
									     the act that makes something stands where every row's act stands. -->
									<span class="presses">
										<Button
											size="small"
											tone="ghost"
											disabled={busy !== null}
											onclick={() => {
												removing = { id: entry.entry_id, name: entry.name };
												removeOpen = true;
											}}
										>
											{COPY.waiting.remove}
										</Button>
										<Button
											size="small"
											busy={busy === entry.entry_id}
											disabled={busy !== null || !enabled}
											onclick={() => void create(entry.entry_id, entry.name)}
										>
											{COPY.waiting.create}
										</Button>
									</span>
								</li>
							{/each}
						</ul>
					</Scroller>
				</div>
			{/if}
		</div>
	{/if}
</SettingGroup>

<ConfirmDialog
	bind:open={removeOpen}
	destructive
	title={COPY.waiting.removeTitle(removing?.name ?? '')}
	consequence={COPY.waiting.removeConsequence}
	confirmLabel={COPY.waiting.remove}
	onconfirm={() => {
		if (removing) void remove(removing.id);
	}}
/>

<style>
	.stack {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.count {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* The cap on the box that SCROLLS, which the shared region draws, hence `:global`; the same
	   height as the list of People Sift can recognize above. See `Faces.svelte`. */
	.held-box :global(.scroll-root) {
		max-block-size: var(--settings-list-cap);
	}

	/* The name and its line on the left, the two presses on the right. The padding is the room a
	   press's focus ring needs inside the scroller, which clips at its edge, and the inset the list
	   above starts its names at, so the two lists' names stand on one edge. */
	.held {
		margin: 0;
		padding: var(--space-1) var(--space-3) var(--space-1) var(--space-2);
		list-style: none;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	/* Wrapping where the row is too narrow for both halves: the presses then take a line of their
	   own, still at its end. */
	.held li {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2) var(--space-3);
	}

	.entry {
		display: flex;
		flex-direction: column;
		flex: 1 1 14rem;
		min-inline-size: 0;
	}

	.who {
		color: var(--sift-ink);
	}

	.line {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.presses {
		display: flex;
		gap: var(--space-2);
		flex-shrink: 0;
		margin-inline-start: auto;
	}
</style>
