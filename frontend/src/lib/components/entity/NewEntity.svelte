<script lang="ts">
	/*
	 * A thing that does not exist yet, drawn as the form that edits one.
	 *
	 * NOT ON THE GALLERY: it is a whole screen rather than a component (a page frame with the
	 * record form inside it), and its fields come from the field registry, which is fetched, so an
	 * entry would draw whatever the running install held; `EntityHistory` and `RelatedWall` are
	 * absent for the same reason.
	 *
	 * Add opens the form Edit opens, blank, and the row comes into existence on Save with
	 * everything already on it, so a new thing gets its description, other names, category and
	 * network in one step rather than being made from a name and edited on its own page afterwards.
	 *
	 * The same form, not a lookalike: `RecordForm` over `fields.drawn(subject)`, the component and
	 * field list the entity's own page opens on Edit, so a field added to the registry arrives here
	 * too.
	 *
	 * A kind with no record gets its name and nothing else. A collection has no record (nothing in
	 * `Subject` names one), so its Edit is a box with its name in it, and that is what this draws;
	 * inventing fields would invent a record the server has nowhere to put.
	 *
	 * Nothing is written until Save: there is no row behind this screen, so leaving it makes
	 * nothing. `oncreate` is handed the whole draft and is the one write.
	 */
	import { Avatar, Button, ChooseFile, Field, Problem, TextInput } from '$lib/components/common';
	import type { Crumb } from '$lib/components/common/Breadcrumbs.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import RecordForm from '$lib/components/record/RecordForm.svelte';
	import { ApiError } from '$lib/api/client';
	import { SaveRefused, type RecordSubject } from '$lib/entity/records.svelte';

	interface Props {
		/**
		 * Which record to draw, or absent for a kind that has none.
		 *
		 * Absent is not "unknown": it is the honest answer for a collection, whose whole editable
		 * surface is its name.
		 */
		subject?: RecordSubject;
		/** What one of these is called, in the words on the screen: "tag", "Photo Set". */
		noun: string;
		/**
		 * Make it. Handed the draft with the name already trimmed and known not to be blank, and
		 * the picture somebody chose, where they chose one.
		 *
		 * The picture is handed over rather than uploaded here: a cover is put on a row, and there
		 * is no row until this call makes one. So the page does both, in the order the server needs
		 * (make the thing, then put the picture on it), and this screen stays ignorant of ids and
		 * routes, which is why `oncreate` exists.
		 *
		 * Throwing leaves the form up with everything typed, under this component's own sentence or
		 * the caller's; see `SaveRefused`.
		 */
		oncreate: (draft: Record<string, unknown>, cover: File | null) => Promise<void>;
		/** Where Cancel goes. The wall this was opened from. */
		oncancel: () => void;
		/** The way back to that wall, drawn in the frame's own band. */
		crumbs: Crumb[];
		/** How long the name may be. The server's own limit. */
		maxlength?: number;
	}

	let { subject, noun, oncreate, oncancel, crumbs, maxlength = 120 }: Props = $props();

	/*
	 * The form's own id, so Save and Cancel can be drawn in the page's header as well as at its
	 * foot.
	 *
	 * `<button form="...">` submits a form it is not inside: the same mechanism, for the same
	 * reason, as the Save an entity's own page draws beside its Edit button: the draft stays where
	 * it is written and the header's button is the SAME submit as the one at the bottom. The
	 * alternative is lifting the draft into this screen, which would put it in two places.
	 *
	 * Unique per instance, because the id is what pairs a button with a form and two of these on
	 * one document would otherwise both be the same form.
	 */
	const uid = $props.id();
	const formId = `${uid}-new`;

	/* The heading, and the document's name with it. The noun arrives in the case it is written in
	   on screen ("tag", "Photo Set") so nothing here re-cases it.
	 *
	 * NOT named for the attribute a browser draws its own tooltip from: the gate that keeps that
	 * attribute off the screen reads the word followed by an equals sign anywhere in a file, so a
	 * variable spelt that way reads to it exactly like the attribute it refuses. */
	const heading = $derived(`New ${noun}`);

	/* The name a kind with no record is making. Held here rather than inside a form component,
	   because there is no record to seed a draft from. */
	let name = $state('');
	let busy = $state(false);
	let problem = $state<string | undefined>();

	/*
	 * The picture this thing will be made with, chosen from the disk and not yet anywhere.
	 *
	 * The OTHER half of the pencil on an entity's cover. That control opens `PickPicture`, which
	 * offers this thing's own files and an upload, and on a thing that does not exist yet the
	 * first of those two is empty by definition: nothing has been filed under a site nobody has
	 * made. So what is offered here is the half that can mean something, which is the upload, and
	 * it is the same `ChooseFile` in front of the same kind of File the sheet's own upload sends.
	 *
	 * Held rather than sent, for the reason written over `oncreate`: the cover goes on a row and
	 * there is no row until Save. Leaving the screen therefore uploads nothing, which is the same
	 * promise the rest of this form makes.
	 */
	let cover = $state<File | null>(null);

	/* What the chosen file looks like. An object URL rather than a data URL: it costs no copy of
	   the bytes and it is revoked the moment it stops being the picture, so choosing five in a row
	   holds one. */
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

	/* The last one, when the screen goes. Nothing else revokes it, so without this a page somebody
	   opened and left holds the bytes until the tab is closed. */
	$effect(() => () => {
		if (preview) URL.revokeObjectURL(preview);
	});

	/* The two rules this screen adds to the form under it: a thing has to be called something, and
	 * not something it is already called.
	 *
	 * Here rather than in each of the five pages, and each is a refusal rather than a Save that
	 * cannot be pressed: a disabled button says nothing about why, and the reason is one short
	 * sentence.
	 *
	 * A 409 out of `oncreate` is the name being taken. Every page makes the thing first and writes
	 * the rest of the record inside its own catch, so the create is the only call whose refusal
	 * reaches here, and the only create that answers 409 is one whose kind keeps its names unique
	 * (a tag, a site). Left to the form's flat "That couldn't be saved.", somebody looking at a name
	 * they can see is spelled right would have nothing to go on. */
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

<!--
	THE TWO CONTROLS THAT END THIS SCREEN, at the top and on the right.

	Drawn twice, here and at the foot of the form, and that is the point rather than a duplicate: a
	record long enough to scroll would put the only Save below the fold, so a form somebody had
	finished filling in would offer nothing to press without scrolling back down. An entity's own
	page draws the pair up here in edit mode; this is the same pair, in the same order, at the
	same end of the same row: Cancel, then Save at the right-hand edge.

	`busy` is deliberately NOT mirrored from the record form. The form owns whether it is saving and
	nothing outside it can know; a submit pressed twice is refused by the form's own guard, which is
	where that question has always been answered.
-->
{#snippet ends()}
	<Button type="button" onclick={() => oncancel()}>Cancel</Button>
	<Button type="submit" form={formId} icon="save" tone="primary">Save</Button>
{/snippet}

<PageFrame {crumbs} header={titleRow}>
	{#snippet children()}
		<!-- The picture, first, above whatever fields this kind has.

		     First because it is what the thing will be RECOGNISED by and because it is the one
		     control here that is not a box of text: put at the end it reads as an afterthought
		     under a column of fields, which is how a cover gets left off. -->
		{@render cover_()}

		{#if subject}
			<!-- The record form, with no values behind it. Every box opens empty and the row is made
			     out of what is in them when Save is pressed. -->
			<RecordForm
				{subject}
				{formId}
				label={heading}
				values={{}}
				onsave={make}
				oncancel={() => oncancel()}
			/>
		{:else}
			<!-- A kind with no record: a name box, on a page of its own so that Add means one thing
			     on all five walls. -->
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
				<!-- On the right and in the header's order, for the reason `RecordForm`'s own
				     closing row gives. -->
				<div class="close">
					<Button type="button" onclick={() => oncancel()} disabled={busy}>Cancel</Button>
					<Button type="submit" tone="primary" icon="save" {busy}>Save</Button>
				</div>
			</form>
		{/if}
	{/snippet}
</PageFrame>

<!--
	Choosing the picture, on the screen that makes the thing, so a new thing can have its cover from
	the start.

	The upload half of the pencil's sheet, not the sheet itself: `PickPicture` offers the thing's
	own files first, and a thing that does not exist has none, so the sheet would be an empty wall
	with one button under it. The button is what is offered.

	Named `cover_` rather than `cover`, because a snippet and the state it draws cannot share a
	name, and the state is the thing somebody chose.
-->
{#snippet cover_()}
	<div class="picture">
		<div class="thumb">
			<!-- The same component the wall and the header draw a cover with, so the picture is
			     framed here exactly as it will be once it is on the row. `instead` is not passed:
			     there is no second address to fall back to, and with no file chosen the letter this
			     draws is the one the thing will wear until somebody gives it a picture. -->
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
	/* The same column, the same gaps and the same closing row as `RecordForm`, so the two screens
	   are one screen with a different number of fields on it rather than two designs. */
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

	/* The picture beside the button that changes it, rather than above it: the preview is small and
	   a column of two short things wastes the width this screen has plenty of. */
	.picture {
		display: flex;
		align-items: center;
		gap: var(--space-4);
		margin-block-end: var(--space-6);
	}

	/* The cover's own shape, at a token's width: `Avatar` takes its size from whatever holds it,
	   and the 3:4 is the component's. Rounded here because a cover is rounded everywhere it is
	   drawn and `Avatar` inherits the radius from what clips it. */
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
