<script lang="ts">
	/*
	 * One proposed shoot, on a page of its own: every picture of it as a wall, with the card's
	 * question and its answers over them.
	 *
	 * A card on the Shoots wall shows a dozen pictures and the rest behind a press, which is enough
	 * to recognise a sitting and not always enough to judge it. This is the card opened up, at an
	 * address of its own the way a Photo Set opens, so every picture can be looked at before
	 * answering and the address survives a reload.
	 *
	 * The question, its detail and the answers are the card's own, from the one builder the wall's
	 * card is built by (`GET /shoots/{id}`, which shares the list's builder), and pressing a picture
	 * opens the viewer on the whole shoot, as the card does. An answer made here goes back to the
	 * wall, because the shoot it was about is no longer waiting.
	 */
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { thumbUrl } from '$lib/entity/art';
	import { openAsset } from '$lib/player/asset-view';
	import { ApiError, isMissing } from '$lib/api/client';
	import {
		ConfirmDialog,
		Empty,
		Field,
		Pressable,
		Problem,
		Skeleton,
		TextInput
	} from '$lib/components/common';
	import Icon from '$lib/components/Icon.svelte';
	import Answers from '$lib/components/organize/Answers.svelte';
	import DecisionCard from '$lib/components/organize/DecisionCard.svelte';
	import OrganizeHeader from '$lib/components/organize/OrganizeHeader.svelte';
	import Said from '$lib/components/organize/Said.svelte';
	import { shootAnswers } from '$lib/components/organize/ShootsPanel.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import { organizeCrumbs } from '$lib/organize/bands';
	import { heldBoard, answered } from '$lib/organize/organize.svelte';
	import { makeTheSet, nameTheRest, notASet, shoot, type Shoot } from '$lib/entity/shoots.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';

	const id = $derived(page.params.id ?? '');

	let one = $state<Shoot | null>(null);
	let loading = $state(true);
	let missing = $state(false);
	let failed = $state(false);
	let busy = $state(false);
	let refuseOpen = $state(false);
	let nameOpen = $state(false);
	let typed = $state('');
	/* The pictures whose still could not be fetched, drawn as the glyph a tile wears for it. */
	let unseen = $state<Set<string>>(new Set());

	async function load() {
		if (!id) return;
		loading = true;
		failed = false;
		missing = false;
		try {
			one = await shoot(id);
		} catch (error) {
			missing = isMissing(error);
			failed = !missing;
			one = null;
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		void id;
		void load();
	});

	const heading = $derived(one ? `Photos of ${one.name}` : 'A shoot');

	/* An answer settles the shoot, so the page it was on is the wall it came from. */
	async function settle(act: () => Promise<unknown>, refused: string): Promise<void> {
		busy = true;
		try {
			await act();
			answered.changed();
			void goto('/organize/shoots');
		} catch (failure) {
			if (failure instanceof ApiError && failure.status === 409 && failure.detail) {
				/* Its pictures went into a Photo Set while it stood: refused, and answered by that
				   set, so this shoot is settled all the same and the page goes back to the wall. */
				toasts.show(failure.detail);
				answered.changed();
				void goto('/organize/shoots');
				return;
			}
			toasts.show(failure instanceof ApiError && failure.detail ? failure.detail : refused, {
				tone: 'error'
			});
		} finally {
			busy = false;
		}
	}

	function look(shown: Shoot, pictureId: string): void {
		openAsset(
			pictureId,
			shown.items.map((item) => ({ id: item.id, runs: item.media_type !== 'image' }))
		);
	}

	function missed(pictureId: string): void {
		unseen = new Set([...unseen, pictureId]);
	}
</script>

<svelte:head><title>{heading}</title></svelte:head>

<PageFrame crumbs={organizeCrumbs(heldBoard.found?.queues ?? [], 'shoots', heading)}>
	{#snippet header()}
		<OrganizeHeader queue="shoots" here={heading}></OrganizeHeader>
	{/snippet}

	{#if loading && one === null}
		<Skeleton lines={3} />
	{:else if missing}
		<Empty scope="page" icon="photo_library" title="That shoot is no longer waiting">
			It was answered, or its photos are no longer loose. What is still waiting is in Shoots.
		</Empty>
	{:else if failed}
		<Problem message="That shoot couldn't be loaded. Try again in a moment." />
	{:else if one}
		{@const shown = one}
		<DecisionCard>
			{#snippet question()}<Said what={shown.question} links={shown.links} />{/snippet}
			{#snippet detail()}{shown.detail}{/snippet}
			<ul class="wall" aria-label={heading}>
				{#each shown.items as picture, at (picture.id)}
					<li class:unnamed={!picture.named}>
						<Pressable
							class="look"
							feedback="none"
							radius="sm"
							aria-label="Open photo {at + 1} of {shown.items.length}"
							onclick={() => look(shown, picture.id)}
						>
							{#if unseen.has(picture.id)}
								<span class="none"><Icon name="hide_image" size={20} label="No preview" /></span>
							{:else}
								<img
									src={thumbUrl({ id: picture.id, art: picture.art })}
									alt=""
									loading="lazy"
									decoding="async"
									onerror={() => missed(picture.id)}
								/>
							{/if}
						</Pressable>
					</li>
				{/each}
			</ul>
			{#snippet answers()}
				{@const given = shootAnswers(shown, {
					make: () => void settle(() => makeTheSet(shown.id), "That Photo Set couldn't be created"),
					makeNamed: () => {
						typed = shown.name;
						nameOpen = true;
					},
					nameRest: () =>
						void settle(() => nameTheRest(shown.id), "Those photos couldn't be named"),
					discard: () => (refuseOpen = true)
				})}
				<Answers yes={given.yes} rest={given.rest} about="this shoot" disabled={busy} />
			{/snippet}
		</DecisionCard>
	{/if}
</PageFrame>

<ConfirmDialog
	bind:open={refuseOpen}
	title="Discard this shoot?"
	consequence="Sift won't suggest these photos as a shoot again, in any grouping. Nothing is
	deleted or moved, and you can still add them to a Photo Set yourself."
	confirmLabel="Discard"
	destructive={false}
	onconfirm={() => {
		if (one) void settle(() => notASet(one!.id), "That shoot couldn't be discarded");
	}}
/>

<ConfirmDialog
	bind:open={nameOpen}
	title="Create a Photo Set with a name"
	consequence="The photos of this shoot become one Photo Set with this name. You can rename it later on its page."
	confirmLabel="Create"
	destructive={false}
	confirmDisabled={typed.trim().length === 0}
	onconfirm={() => {
		if (one) void settle(() => makeTheSet(one!.id, typed), "That Photo Set couldn't be created");
	}}
>
	{#snippet extra()}
		<Field label="Name">
			{#snippet control({ id: fieldId, describedBy })}
				<TextInput id={fieldId} bind:value={typed} autocomplete="off" {describedBy} />
			{/snippet}
		</Field>
	{/snippet}
</ConfirmDialog>

<style>
	/* Every picture of the shoot, as the card's strip is drawn, larger: the page is for looking. */
	.wall {
		list-style: none;
		margin: var(--space-3) 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(9rem, 100%), 1fr));
		gap: var(--space-2);
	}

	.wall :global(.look) {
		display: block;
		inline-size: 100%;
		padding: 0;
	}

	.wall img {
		display: block;
		inline-size: 100%;
		aspect-ratio: 1;
		object-fit: cover;
		border-radius: var(--radius-sm);
	}

	.wall .none {
		display: grid;
		place-items: center;
		aspect-ratio: 1;
		color: var(--sift-ink-3);
	}

	/* A picture carrying nobody is marked, as on the card: it is what "Name the rest" is about. */
	.wall li.unnamed img {
		outline: 2px dashed var(--sift-line);
		outline-offset: -2px;
	}
</style>
