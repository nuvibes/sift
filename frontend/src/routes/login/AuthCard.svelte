<script lang="ts">
	/*
	 * The signed-out screen: one card, centred, on an empty page.
	 *
	 * Sign-in and first-run setup are the same three fields and differ only in what the server does
	 * with them, so they are one component with different words rather than two screens that drift.
	 *
	 * No step counter: first run is an account and an installation method and nothing else, so a
	 * counter would be one of one, and a progress bar that can only ever be full says nothing.
	 * Everything else is answered in Settings, or stays at the default it ships with.
	 *
	 * Two things it must not do. It must not say whether a username exists: the server already
	 * answers "Incorrect username or password." to both, and a screen that said "no such user"
	 * would undo that. And it must not validate the password as you type: telling somebody their
	 * password is too short while they are still typing it is both wrong and irritating.
	 */
	import { goto } from '$app/navigation';
	import {
		BackButton,
		Button,
		DoorCard,
		Field,
		Note,
		PasswordInput,
		PasswordStrength,
		Problem,
		TextInput
	} from '$lib/components/common';
	import { bridge } from '$lib/bridge';
	import { api, ApiError } from '$lib/api/client';
	import { session, type Viewer } from '$lib/shell/session.svelte';

	interface Props {
		/** 'setup' creates the one admin; 'login' signs in to an existing account. */
		mode: 'setup' | 'login';
	}

	let { mode }: Props = $props();

	/*
	 * Whether there is a question BEHIND this one.
	 *
	 * Only in the desktop app, and only while creating the account: the step before it is the shell
	 * asking where the library is kept, and going back means unsaying that. In a browser there is
	 * nothing behind this screen at all (the shell questions were answered on another computer),
	 * so no button is drawn rather than one that would refuse.
	 */
	const canGoBack = $derived(mode === 'setup' && bridge.canSetUp());

	let username = $state('');
	let password = $state('');
	let confirmation = $state('');
	let error = $state<string | null>(null);
	let busy = $state(false);

	const heading = $derived(mode === 'setup' ? 'Create your Sift admin account' : 'Sign in');
	const action = $derived(mode === 'setup' ? 'Create account' : 'Sign in');

	async function goBack() {
		if (busy) return;
		busy = true;
		error = null;
		const settled = await bridge.setupBack();
		/* Nothing is put back on `ok`: the shell is already drawing the question before this one. */
		if (settled.ok) return;
		error = settled.refusal;
		busy = false;
	}

	async function submit(event: Event) {
		event.preventDefault();
		error = null;

		if (mode === 'setup' && password !== confirmation) {
			error = "Those two passwords aren't the same.";
			return;
		}

		busy = true;
		try {
			const viewer = await api.post<Viewer>(mode === 'setup' ? '/auth/setup' : '/auth/login', {
				body: { username, password }
			});
			session.adopt(viewer);
			await goto('/browse');
		} catch (caught) {
			if (caught instanceof ApiError && caught.status === 422 && mode === 'setup') {
				// The server owns the password policy; it says what is wrong with this one.
				error = caught.detail ?? "That password isn't strong enough.";
			} else if (caught instanceof ApiError && caught.status === 409) {
				error = 'This instance already has an admin. Sign in instead.';
			} else if (caught instanceof ApiError && mode === 'login') {
				/*
				 * One sentence for every way a sign-in can fail, and it is deliberately vague.
				 *
				 * Leaving something out is a 422 and the shared message for that is "some of those
				 * details were not valid", which reads as a form complaining about its own
				 * formatting, when what happened is that the sign-in did not work. A wrong password
				 * is a 401 and would say something different again, so the screen would have two
				 * voices for one outcome.
				 *
				 * It does not say WHICH was wrong, and that is the point rather than an omission: an
				 * error naming the username as the valid half is a way to work out which accounts
				 * exist, one guess at a time.
				 */
				error = 'Incorrect username or password.';
			} else if (caught instanceof ApiError) {
				error = caught.message;
			} else {
				error = "That didn't work.";
			}
		} finally {
			busy = false;
		}
		/* WHAT WAS TYPED IS KEPT WHEN THE ATTEMPT FAILED.
		 *
		 * Clearing the password boxes on every refusal (a policy the password missed by one
		 * character, a server that was not there) would make somebody retype a long password
		 * AND its confirmation before they could try again, with a rule list on screen telling
		 * them what to change about a password that is not in front of them any more.
		 *
		 * There is nothing to clear on the way out on success: the window has already left for
		 * the library, and this component goes with it.
		 */
	}
</script>

<!-- The heading is DRAWN on the setup side only. "Sign in" over two fields and a button reading
     "Sign in" is the same word twice; "Create your Sift admin account" says what the button
     cannot. It is still the page's h1 either way. See `drawHeading`. -->
<DoorCard {heading} drawHeading={mode === 'setup'} onsubmit={submit}>
	<Field label="Username">
		{#snippet control({ id, describedBy })}
			<TextInput
				{id}
				type="text"
				name="username"
				bind:value={username}
				{describedBy}
				autocomplete="username"
				autocapitalize="none"
				spellcheck="false"
				required
			/>
		{/snippet}
	</Field>

	<Field label="Password">
		{#snippet control({ id, describedBy })}
			<PasswordInput
				{id}
				{describedBy}
				name="password"
				bind:value={password}
				autocomplete={mode === 'setup' ? 'new-password' : 'current-password'}
			/>
		{/snippet}
	</Field>

	{#if mode === 'setup'}
		<!-- Only while one is being CHOSEN. On a sign-in the password already exists and the
		     server is about to say whether it is right, so a meter would be commentary on
		     something nobody can change from here. -->
		<PasswordStrength {password} />

		<Field label="Password again">
			{#snippet control({ id, describedBy })}
				<PasswordInput
					{id}
					{describedBy}
					name="confirmation"
					bind:value={confirmation}
					autocomplete="new-password"
				/>
			{/snippet}
		</Field>

		<!--
			Under the password it is about, where it is read while the password is being chosen.

			It is the ONLY user and it is an admin, so a password handed to somebody else is the
			whole library handed to them, and a guest gets an account of their own instead. A
			caution rather than a plain aside for that reason.
		-->
		<Note tone="caution">
			Keep this username and password to yourself. This account can see and change everything in
			Sift. To let another person in, add them as a guest later, in Settings > User Management.
		</Note>
	{/if}

	<!-- The form's answer, in the one shape every screen says a refusal in, announced, because
	     somebody using a screen reader has no other way to know the form came back with something
	     to say. -->
	<Problem message={error} />

	<!-- Busy the way every button is busy: the spinner beside the same words, which stay, because
	     "what did I just press" is the question a spinner alone cannot answer. As wide as the fields
	     above it, the one shape the door's press has on every card that opens Sift. -->
	<Button tone="primary" type="submit" full {busy}>{action}</Button>

	{#if canGoBack}
		<!-- Step one of five, with a way back to the two questions before it. Through the shell
		     rather than through history: the answers that led here are saved, so a history step would
		     redraw a question that has already been answered and pressing it again would do nothing. -->
		<BackButton label="Where Sift keeps your data" onback={() => void goBack()} />
	{/if}
</DoorCard>
