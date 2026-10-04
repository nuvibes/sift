<script lang="ts">
	/* A tab's wall with usernames drawn under its cards, wired the way the two pages wire it.

	   A person's Sites tab and a Site's People tab each hand `RelatedWall` an `under` snippet that
	   draws `UsernameLines` for the card's row, from usernames filed by that card's id. The pages
	   themselves are routes and cannot be mounted on their own, so this is that wiring minus the
	   page: the join is the thing worth testing: the wall knows the rows, the page knows the
	   usernames, and neither half alone draws a username under a card. */
	import RelatedWall from './RelatedWall.svelte';
	import UsernameLines from './UsernameLines.svelte';
	import { usernamesBy, type Username } from '$lib/people/usernames.svelte';
	import type { EntityKind } from '$lib/entity/related.svelte';

	interface Props {
		on: EntityKind;
		showing: 'sites' | 'people';
		/** Every username the page read, before the filing, so the file-less ones reach the rule. */
		usernames: Username[];
	}

	let { on, showing, usernames }: Props = $props();

	const filed = $derived(usernamesBy(usernames, showing === 'sites' ? 'site_id' : 'person_id'));
</script>

<RelatedWall {on} id="subject" {showing} title="Tab" icon="language">
	{#snippet under(row)}
		<UsernameLines usernames={filed.get(row.id) ?? []} />
	{/snippet}
</RelatedWall>
