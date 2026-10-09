<script lang="ts">
	/* The desktop window's first frame, drawn from the shell's own copy of the client while the
	 * real page loads beneath it (`desktop/src/opening.ts`): the sign-in card's outline in the
	 * saved theme, inert, so the real sign-in takes its place with nothing moving. */
	import { Button, DoorCard, Field, PasswordInput, TextInput } from '$lib/components/common';

	/* Always the desktop window, whose bar this frame draws as the real page does, though it has no
	 * bridge to say so (`markOverlaidWindow`). */
	document.documentElement.setAttribute('data-window', 'overlaid');

	try {
		const handed = decodeURIComponent(location.hash.slice(1));
		if (handed !== '') {
			JSON.parse(handed);
			localStorage.setItem('sift.theme', handed);
		}
	} catch {
		/* Not a mirror, or no storage: the default theme, which is right for a first start. */
	}
</script>

<svelte:head>
	<title>Sift</title>
</svelte:head>

<div inert>
	<DoorCard heading="Sign in" drawHeading={false}>
		<Field label="Username">
			{#snippet control({ id, describedBy })}
				<TextInput {id} type="text" {describedBy} />
			{/snippet}
		</Field>
		<Field label="Password">
			{#snippet control({ id, describedBy })}
				<PasswordInput {id} {describedBy} value="" />
			{/snippet}
		</Field>
		<Button tone="primary" type="button" full>Sign in</Button>
	</DoorCard>
</div>
