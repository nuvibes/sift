<script lang="ts">
	/* You, and the things you can do about being you: change your name, change the password, set
	 * the PIN, and sign out. */
	import { onMount } from 'svelte';
	import {
		Button,
		FormCard,
		PasswordInput,
		PasswordStrength,
		PinBox,
		TextInput
	} from '$lib/components/common';
	import { PIN_DIGITS } from '$lib/components/common/PinBox.svelte';
	import { api, ApiError } from '$lib/api/client';
	import { session } from '$lib/shell/session.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { vault } from '$lib/shell/vault.svelte';
	import { signOut } from '$lib/shell/sign-out';
	import ActionRow from './ActionRow.svelte';
	import FactRow from './FactRow.svelte';
	import FieldRow from './FieldRow.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import { PASSWORD, PIN, SIGN_OUT, USERNAME, WHO } from './Profile.search';

	let oldPassword = $state('');
	let newPassword = $state('');
	let confirmation = $state('');
	let busy = $state(false);
	/* One error, shown against the field it belongs to. */
	let oldError = $state<string | undefined>(undefined);
	let newError = $state<string | undefined>(undefined);

	const role = $derived(session.viewer?.role === 'admin' ? WHO.admin : WHO.guest);

	// Seeded from whoever is signed in once the answer arrives, so the box starts at the current
	// name rather than empty: somebody correcting a typo edits it instead of retyping it.
	let newName = $state('');
	let nameError = $state<string | undefined>(undefined);
	let renaming = $state(false);

	$effect(() => {
		if (session.viewer && !newName) newName = session.viewer.username;
	});

	/* Each form's press is lit only while that form has something to save, so the pane at rest
	   carries no lit primary at all and a typed form is the one that is. */
	const nameChanged = $derived(
		newName.trim() !== '' && newName.trim() !== (session.viewer?.username ?? '')
	);
	const passwordReady = $derived(oldPassword !== '' && newPassword !== '' && confirmation !== '');

	/* The PIN form. */
	let pinPassword = $state('');
	let newPin = $state('');
	let pinError = $state<string | null>(null);
	let savingPin = $state(false);

	/* Whether a PIN is set at all, which is the only thing this form reads. */
	onMount(() => void vault.load());

	/* Through the vault's one PIN write, the same one Hidden's first-press dialog uses, so the
	   two forms cannot come to disagree about what a PIN is or what a refusal means. */
	async function savePin(event: SubmitEvent) {
		event.preventDefault();
		if (savingPin || newPin.length !== PIN_DIGITS) return;
		savingPin = true;
		pinError = null;
		try {
			const refused = await vault.setPin(newPin, pinPassword);
			if (refused === 'wrong-password') pinError = "That password isn't correct.";
			else if (refused === 'not-a-pin') pinError = 'A PIN is 6 digits.';
			else {
				pinPassword = '';
				newPin = '';
				toasts.show('PIN saved', { tone: 'success' });
			}
		} catch {
			pinError = "That couldn't be saved.";
		} finally {
			savingPin = false;
		}
	}

	async function renameSelf(event: SubmitEvent) {
		event.preventDefault();
		const viewer = session.viewer;
		if (renaming || !viewer) return;
		nameError = undefined;
		renaming = true;
		try {
			await api.post(`/auth/users/${viewer.id}/username`, { body: { username: newName } });
			// Re-read rather than assumed: the name on screen everywhere else comes from here.
			await session.load();
			toasts.show('Name changed', { tone: 'success' });
		} catch (error) {
			/* The server's own words, not a sentence written here. */
			if (error instanceof ApiError && (error.status === 409 || error.status === 429))
				nameError = error.detail ?? "That name can't be used.";
			else nameError = "That couldn't be saved.";
		} finally {
			renaming = false;
		}
	}

	async function changePassword(event: SubmitEvent) {
		event.preventDefault();
		oldError = undefined;
		newError = undefined;

		if (newPassword !== confirmation) {
			newError = "The two new passwords don't match.";
			return;
		}

		busy = true;
		try {
			// The server re-wraps the account's master key and revokes every OTHER session, keeping
			// this one, so a password change does not sign you out of the browser you changed it in.
			await api.post('/auth/password', {
				body: { old_password: oldPassword, new_password: newPassword }
			});
			oldPassword = '';
			newPassword = '';
			confirmation = '';
			toasts.show('Password changed. Other devices have been signed out.', { tone: 'success' });
		} catch (error) {
			if (error instanceof ApiError && error.status === 401) {
				oldError = "That isn't your current password.";
			} else if (error instanceof ApiError && error.status === 422) {
				// The policy sentence is written for the person to act on, so it is shown as-is.
				newError = error.detail ?? "That password doesn't meet the requirements.";
			} else {
				toasts.show("That couldn't be saved", { tone: 'error' });
			}
		} finally {
			busy = false;
		}
	}
</script>

<!-- The pane's own rows, group by group: the forms are rows too (`FormCard`), so nothing here is
     a card standing in a column of rows. -->
{#if session.viewer}
	<SettingGroup id="profile.who">
		<FactRow label={WHO.label} help={role} fact={session.viewer.username} />
	</SettingGroup>
{/if}

<!-- Renaming yourself, here rather than only under Settings. -->
<SettingGroup id="profile.name" heading={USERNAME.name} help={USERNAME.lede}>
	<FormCard onsubmit={renameSelf}>
		<FieldRow label="Username" error={nameError}>
			{#snippet control({ id, describedBy, invalid })}
				<TextInput
					{id}
					{describedBy}
					{invalid}
					type="text"
					autocomplete="username"
					autocapitalize="none"
					spellcheck="false"
					bind:value={newName}
				/>
			{/snippet}
			{#snippet press()}
				<Button
					type="submit"
					tone="primary"
					icon="edit_square"
					busy={renaming}
					disabled={!nameChanged}>Rename</Button
				>
			{/snippet}
		</FieldRow>
	</FormCard>
</SettingGroup>

<!-- Two sentences, and which one you get depends on what is true for you. -->
<SettingGroup
	id="profile.password"
	heading={PASSWORD.name}
	help={session.isAdmin ? PASSWORD.ledeAdmin : PASSWORD.ledeGuest}
>
	<FormCard onsubmit={changePassword}>
		<FieldRow label="Current password" error={oldError}>
			{#snippet control({ id, describedBy, invalid })}
				<PasswordInput
					{id}
					{describedBy}
					{invalid}
					autocomplete="current-password"
					bind:value={oldPassword}
				/>
			{/snippet}
		</FieldRow>

		<FieldRow label="New password" error={newError}>
			{#snippet control({ id, describedBy, invalid })}
				<PasswordInput {id} {describedBy} {invalid} bind:value={newPassword} />
			{/snippet}
			{#snippet under()}
				<PasswordStrength password={newPassword} />
			{/snippet}
		</FieldRow>

		<FieldRow label="New password again">
			{#snippet control({ id, describedBy })}
				<PasswordInput {id} {describedBy} bind:value={confirmation} />
			{/snippet}
			{#snippet press()}
				<Button type="submit" tone="primary" icon="save" {busy} disabled={!passwordReady}
					>Save password</Button
				>
			{/snippet}
		</FieldRow>
	</FormCard>
</SettingGroup>

<!--
	Your PIN, after your password. The head of this file says why it is here and why the argument for
	Privacy is a real one to lose.
-->
<SettingGroup id="profile.pin" heading={PIN.name} help={vault.pinSet ? PIN.ledeSet : PIN.ledeUnset}>
	<FormCard onsubmit={savePin}>
		<FieldRow
			label="Your password"
			help="So that no one can change your PIN from a screen you left signed in."
		>
			{#snippet control({ id, describedBy })}
				<TextInput
					{id}
					{describedBy}
					type="password"
					autocomplete="current-password"
					bind:value={pinPassword}
				/>
			{/snippet}
		</FieldRow>

		<FieldRow label="New PIN" help="Six digits." error={pinError ?? undefined}>
			{#snippet control({ id, describedBy, invalid })}
				<!-- The shared PIN control. See `PinBox`. -->
				<PinBox {id} {describedBy} {invalid} bind:value={newPin} />
			{/snippet}
			{#snippet press()}
				<Button
					type="submit"
					tone="primary"
					icon={vault.pinSet ? 'save' : 'add'}
					disabled={savingPin || !pinPassword || newPin.length !== PIN_DIGITS}
				>
					{savingPin ? 'Saving\u2026' : vault.pinSet ? 'Save PIN' : 'Create PIN'}
				</Button>
			{/snippet}
		</FieldRow>
	</FormCard>

	<p class="note">{PIN.after}</p>
</SettingGroup>

<SettingGroup heading={SIGN_OUT.name}>
	<ActionRow
		id="profile.sign-out"
		label={SIGN_OUT.row}
		help={session.isAdmin ? SIGN_OUT.help : SIGN_OUT.helpGuest}
		action={SIGN_OUT.action}
		onclick={() => void signOut()}
	/>
</SettingGroup>

<style>
	/* The sentence UNDER a form rather than the one above it: smaller and quieter, because it is
	   something to read afterwards rather than the thing that introduces the control. */
	.note {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}
</style>
