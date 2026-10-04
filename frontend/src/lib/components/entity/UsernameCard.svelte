<script lang="ts">
	/*
	 * A username nobody has said who it belongs to, as a card: the one shape it has wherever it is
	 * asked about (the Usernames waiting pile under Organize, and a Site's People tab after the
	 * people).
	 *
	 * The picture and the name open the username's files (the Files wall filtered with
	 * `?username=`, exactly the set the server filed under this row), and "Open the files" says so
	 * for anybody who does not try the picture. "Choose person" opens the same picker "Add as
	 * person" opens on Faces: `PickMenu` over the whole library, each person drawn by their face,
	 * and its create row making somebody new from what was typed.
	 *
	 * The username is passed as the creator name, so the picture a download or a stash-box kept for
	 * it is drawn, and otherwise its letter.
	 */
	import { goto } from '$app/navigation';
	import EntityCard from '$lib/components/entity/EntityCard.svelte';
	import { Button, MenuButton, PickMenu } from '$lib/components/common';
	import { usernames, type Username } from '$lib/people/usernames.svelte';
	import { askPeople, makePerson } from '$lib/people/people-picker';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';

	interface Props {
		one: Username;
		/** The line under the name: what this card is asking, or how many files, as the screen says it. */
		detail: string;
		/** The Site's mark over the picture's foot, where the screen is not already the Site. */
		sites?: readonly { name: string; src: string }[];
		/** Told once the username belongs to somebody, so the screen reads itself again. */
		onjoined?: () => void;
	}

	let { one, detail, sites = [], onjoined }: Props = $props();

	/* A second press on a card about to leave its list would join the row twice. */
	let busy = $state(false);

	function shownAs(row: Username): string {
		return row.display_name || row.username;
	}

	function filesOf(row: Username): string {
		return `/browse?username=${encodeURIComponent(row.id)}`;
	}

	/* The join writes the username onto them as an also-known-as name (`as_alias`, the server's
	   default), and its line on their History. */
	async function join(personId: string) {
		if (busy) return;
		busy = true;
		try {
			const joined = await usernames.attach(one.id, { personId });
			toasts.show(
				[
					thing('username', one.id, shownAs(one)),
					' is ',
					joined.person_id ? thing('person', joined.person_id, joined.person_name ?? '') : 'them'
				],
				{ tone: 'success' }
			);
			onjoined?.();
		} catch {
			toasts.show("That couldn't be assigned", { tone: 'error' });
		} finally {
			busy = false;
		}
	}
</script>

<!-- No cover columns handed over: a username has no page, so no cover can be chosen for it, and the
     card's cover address is built from `href`, which is the Files wall here. -->
<EntityCard href={filesOf(one)} name={shownAs(one)} creatorName={one.username} {sites} {detail}>
	{#snippet beneath()}
		<div class="ask">
			{#if one.number}
				<!-- "ID", the one word for a Site's number for a username. -->
				<p class="number">ID {one.number}</p>
			{/if}
			<!-- The act on the right, the quiet door on its left: every card ends on its affirmative.
			     A button and not a link for the files: Sift's buttons are never links. -->
			<div class="answers">
				<Button tone="ghost" size="small" onclick={() => void goto(filesOf(one))}
					>Open the files</Button
				>
				<MenuButton label="Choose who {shownAs(one)} is" disabled={busy} scrolls={false}>
					{#snippet trigger({ props })}
						<Button {...props} tone="primary" size="small" {busy} type="button"
							>Choose person</Button
						>
					{/snippet}
					<PickMenu
						label="Add as person"
						icon="person_add"
						kind="person"
						plural="people"
						inline
						ask={askPeople}
						onpick={(choice) => void join(choice.id)}
						oncreate={makePerson}
					/>
				</MenuButton>
			</div>
		</div>
	{/snippet}
</EntityCard>

<style>
	.ask {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		/* Pinned to the card's foot, so the ID and the presses stand level across a row of cards
		   whose sentences take one line or two. */
		margin-block-start: auto;
		padding-block-start: var(--space-1);
	}

	.number {
		margin: 0;
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
		color: var(--sift-ink-3);
	}

	/* On the right, where an action goes on a row or a card. */
	.answers {
		display: flex;
		flex-wrap: wrap;
		justify-content: flex-end;
		gap: var(--space-2);
	}

	/* On a phone a wall is three cards across, each about 110px wide, and "Choose person" is wider
	   than that: pushed to the right edge it would run out of the card's left side. There the two
	   presses are the card's full width, one above the other, and the words may take two lines. */
	@media (max-width: 767px) {
		.answers {
			flex-direction: column;
			align-items: stretch;
		}

		.answers :global(.btn) {
			inline-size: 100%;
			white-space: normal;
		}
	}
</style>
