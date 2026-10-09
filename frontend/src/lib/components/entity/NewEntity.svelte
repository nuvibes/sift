<script lang="ts">
	/*
	 * A thing that does not exist yet, drawn as the form that edits one (RecordForm, blank); the
	 * row is created on Save with everything on it, and nothing is written before.
	 * NOT ON THE GALLERY: it is a whole screen rather than a component (a page frame with the
	 * record form inside it), and its fields are fetched from the running install.
	 */
	import { Avatar, Button, ChooseFile, Field, Problem, TextInput } from '$lib/components/common';
	import type { Crumb } from '$lib/components/common/Breadcrumbs.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import RecordForm from '$lib/components/record/RecordForm.svelte';
	import { ApiError } from '$lib/api/client';
	import { SaveRefused, type RecordSubject } from '$lib/entity/records.svelte';

	interface Props {
		/** Which record to draw; absent for a collection, whose whole surface is its name. */
		subject?: RecordSubject;
		/** What one of these is called, in the words on the screen: "tag", "Photo Set". */
		noun: string;
		/** Create it from the trimmed draft and the chosen picture, which needs the row first. */
		oncreate: (draft: Record<string, unknown>, cover: File | null) => Promise<void>;
		/** Where Cancel goes. The wall this was opened from. */
		oncancel: () => void;
		/** The way back to that wall, drawn in the frame's own band. */
		crumbs: Crumb[];
		/** How long the name may be. The server's own limit. */
		maxlength?: number;
	}

	let { subject, noun, oncreate, oncancel, crumbs, maxlength = 120 }: Props = $props();

	/* The form's id, so the header's Save is the same submit as the foot's. */
	const uid = $props.id();
	const formId = `${uid}-new`;

	/* The heading and the document's name. Not named for the tooltip attribute: its gate reads
	   that word followed by an equals sign anywhere. */
	const heading = $derived(`New ${noun}`);

	/* The name, for a kind with no record to seed a draft from. */
	let name = $state('');
	let busy = $state(false);
	let problem = $state<string | undefined>();

	/* The chosen picture, held, not sent: it goes on the row Save creates. */
	let cover = $state<File | null>(null);

	/* An object URL, revoked when it stops being the picture. */
	let preview = $state<string | undefined>();

	function choose(file: File): void {
		if (preview) URL.revokeObjectURL(preview);
		cover = file;
		preview = URL.createObjectURL(file);
	}

	function unchoose(): void {
		if (preview) URL.revokeObjectURL(preview);
		cover = null;
		preview = undefined;
	}

	/* The last one, revoked when the screen goes. */
	$effect(() => () => {
		if (preview) URL.revokeObjectURL(preview);
	});

	/* A name is required and not taken: a 409 from `oncreate` is the name in use. */
	async function make(draft: Record<string, unknown>): Promise<void> {
		const typed = String(draft.name ?? '').trim();
		if (!typed) throw new SaveRefused(`Give the ${noun} a name.`);
		try {
			await oncreate({ ...draft, name: typed }, cover);
		} catch (failure) {
			if (failure instanceof ApiError && failure.status === 409) {
				throw new SaveRefused(`There's already a ${noun} called "${typed}".`);
			}
			throw failure;
		}
	}

	async function makeNamed(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		if (busy) return;
		busy = true;
		problem = undefined;
		try {
			await make({ name });
		} catch (failure) {
			problem = failure instanceof SaveRefused ? failure.message : 'That could not be saved.';
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>{heading}</title></svelte:head>

{#snippet titleRow()}
	<PageHeader title={heading} controls={ends} />
{/snippet}

<!-- Cancel and Save in the header too, so a long form needs no scroll; `busy` stays the form's. -->

{#snippet ends()}
	<Button type="button" onclick={() => oncancel()}>Cancel</Button>
	<Button type="submit" form={formId} icon="save" tone="primary">Save</Button>
{/snippet}

<PageFrame {crumbs} header={titleRow}>
	{#snippet children()}
		<!-- The picture first: it is what the thing is recognised by. -->

		{@render cover_()}

		{#if subject}
			<!-- The record form with no values behind it. -->
			<RecordForm
				{subject}
				{formId}
				label={heading}
				values={{}}
				onsave={make}
				oncancel={() => oncancel()}
			/>
		{:else}
			<!-- A kind with no record: a name box, so Add means one thing on all five walls. -->
			<form id={formId} class="named" onsubmit={makeNamed} aria-label={heading}>
				<Field label="Name">
					{#snippet control({ id, describedBy })}
						<!-- svelte-ignore a11y_autofocus: this screen exists to be typed into -->
						<TextInput
							{id}
							bind:value={name}
							{maxlength}
							autocomplete="off"
							autofocus
							{describedBy}
						/>
					{/snippet}
				</Field>
				<Problem message={problem} />
				<!-- On the right, in the header's order. -->
				<div class="close">
					<Button type="button" onclick={() => oncancel()} disabled={busy}>Cancel</Button>
					<Button type="submit" tone="primary" icon="save" {busy}>Save</Button>
				</div>
			</form>
		{/if}
	{/snippet}
</PageFrame>

<!-- The upload half of the cover pencil's sheet: a new thing has no files of its own.
Named `cover_`, since the state is `cover`. -->

{#snippet cover_()}
	<div class="picture">
		<div class="thumb">
			<!-- Avatar, framed as it will be on the row; no `instead` to fall back to. -->
			<Avatar src={preview} name={name || heading} shape="portrait" />
		</div>
		<div class="pick">
			<ChooseFile accept="image/*" icon="upload" onchoose={choose} label="Choose a picture">
				{preview ? 'Change the picture' : 'Choose a picture'}
			</ChooseFile>
			{#if preview}
				<Button type="button" tone="ghost" icon="close" onclick={unchoose}>Remove</Button>
			{/if}
		</div>
	</div>
{/snippet}

<style>
	/* RecordForm's column, gaps and closing row. */
	.named {
		display: flex;
		flex-direction: column;
		gap: var(--space-6);
	}

	.close {
		display: flex;
		justify-content: flex-end;
		gap: var(--space-3);
		padding-block-start: var(--space-4);
		border-block-start: 1px solid var(--sift-line);
	}

	/* The picture beside its button. */
	.picture {
		display: flex;
		align-items: center;
		gap: var(--space-4);
		margin-block-end: var(--space-6);
	}

	/* The cover's shape at a token's width, rounded as covers are. */
	.thumb {
		flex: none;
		inline-size: var(--space-16);
		border-radius: var(--radius-md);
		overflow: hidden;
	}

	.pick {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}
</style>
