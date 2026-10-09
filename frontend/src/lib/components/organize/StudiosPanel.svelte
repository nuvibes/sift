<script lang="ts">
	/*
	 * The studios a stash-box made Sites of that may be one person's own store.
	 *
	 * A box keeps many creators as a studio of their own, named after them. The server reads each
	 * Site a box made by two signs: whether the studio's name is credited on its files, and whether
	 * one of its links is a store page of hers (`kernel/access/creator_studios.py`). Both settle it
	 * and neither settles it; the Sites where they disagree, or are too few to say, are asked here,
	 * one card each, with the numbers the question rests on. Yes moves its files to the username;
	 * No keeps the Site and is remembered. Either is a line in History with an Undo, and the toast
	 * that says it carries the same Undo.
	 */
	import { api } from '$lib/api/client';
	import type { components } from '$lib/api/schema';
	import { Empty, Problem, Skeleton } from '$lib/components/common';
	import Answers from '$lib/components/organize/Answers.svelte';
	import CardWall from '$lib/components/organize/CardWall.svelte';
	import DecisionCard from '$lib/components/organize/DecisionCard.svelte';
	import { counted } from '$lib/entity/entity-counts';
	import { libraryChanges, whenChanged } from '$lib/library/changes.svelte';
	import { undo, undoneLine } from '$lib/organize/organize.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';

	type StudioQuestion = components['schemas']['StudioQuestionView'];
	/* One page holds every question there is, so where it starts is not read. */
	type StudioQuestions = components['schemas']['StudioQuestions'];
	type StudioAnswered = components['schemas']['StudioAnswered'];

	let items = $state<StudioQuestion[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let failed = $state(false);
	/** The card whose answer is on its way, so nothing else on it can be pressed. */
	let busy = $state<string | null>(null);

	async function load(): Promise<void> {
		failed = false;
		try {
			const page = await api.get<StudioQuestions>('/stash-boxes/creator-sites');
			items = page.items;
			total = page.total;
		} catch {
			failed = true;
		} finally {
			loading = false;
		}
	}

	void load();
	whenChanged(libraryChanges, () => void load());

	/** The line under the question: how many files, and how many of them credit the name. */
	function studioDetail(one: StudioQuestion): string {
		const files = one.files === 1 ? '1 file' : `${counted(one.files)} files`;
		return `${files}. ${one.name} is named on ${counted(one.credited)} of ${counted(one.scenes)}.`;
	}

	/** What Yes does, said before it is pressed. */
	function where(one: StudioQuestion): string {
		return one.store
			? `Its store page is ${one.handle} on ${one.site}.`
			: `It links to ${one.handle} on ${one.site}.`;
	}

	async function answer(one: StudioQuestion, path: 'username' | 'site'): Promise<void> {
		busy = one.id;
		try {
			const done = await api.post<StudioAnswered>(
				`/stash-boxes/creator-sites/${encodeURIComponent(one.id)}/${path}`,
				{}
			);
			toasts.show(done.pieces?.length ? done.pieces : done.said, {
				tone: 'success',
				action: {
					label: 'Undo',
					run: () =>
						void undo(done.receipt_id).then((back) => {
							toasts.show(undoneLine(back, 'Undone', "That couldn't be undone"));
							void load();
						})
				}
			});
			await load();
		} catch {
			toasts.show([thing('site', one.id, one.name), " couldn't be changed. Try again."], {
				tone: 'error'
			});
		} finally {
			busy = null;
		}
	}
</script>

<section>
	{#if failed}
		<Problem message="These Sites couldn't be loaded. Refresh the page to try again." />
	{:else if loading && items.length === 0}
		<Skeleton lines={3} />
	{:else if total === 0}
		<Empty scope="page" icon="public" title="No Sites to check">
			When a stash-box creates a Site that may be one person's own store, it appears here.
		</Empty>
	{:else}
		<p class="count">
			{total === 1 ? '1 Site to check' : `${counted(total)} Sites to check`}
		</p>
		<CardWall>
			{#each items as one (one.id)}
				<li>
					<DecisionCard>
						{#snippet question()}Is {one.name} a username on {one.site} rather than a Site?{/snippet}
						{#snippet detail()}{studioDetail(one)}{/snippet}
						<p class="where">{where(one)}</p>
						{#snippet answers()}
							<Answers
								yes={{
									label: 'Yes, a username',
									icon: 'check',
									run: () => void answer(one, 'username')
								}}
								rest={[
									{
										label: "No, it's a Site",
										icon: 'close',
										run: () => void answer(one, 'site')
									}
								]}
								about={one.name}
								busy={busy === one.id}
								disabled={busy !== null && busy !== one.id}
							/>
						{/snippet}
					</DecisionCard>
				</li>
			{/each}
		</CardWall>
	{/if}
</section>

<style>
	/* The queue's size over its wall, drawn as the other Organize walls draw theirs. */
	.count {
		margin: 0 0 var(--space-3);
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	.where {
		margin: 0;
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
		overflow-wrap: anywhere;
	}
</style>
