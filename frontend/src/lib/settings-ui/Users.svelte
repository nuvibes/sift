<script lang="ts">
	/* DRESSED BY: .item, .ui-menu (the shared ContextMenu and ContextMenuItem style the rows and the
	   surface this file hands them, with `:global` from their own files). */

	/* The users who may sign in, and where they come from. */
	import { onMount } from 'svelte';
	import { api, ApiError } from '$lib/api/client';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import { assess } from '$lib/shell/password-strength';
	import { copyText } from '$lib/shell/clipboard';
	import {
		Button,
		ConfirmDialog,
		FormCard,
		PasswordInput,
		PasswordStrength,
		Problem,
		TextInput
	} from '$lib/components/common';
	import ActionRow from './ActionRow.svelte';
	import FieldRow from './FieldRow.svelte';
	import FactRow from './FactRow.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import { GUESTS, RANDOM_GUEST } from './Users.search';
	import { DataRow, DataRows } from '$lib/components/common';
	import type { Verb } from '$lib/components/common/verbs';
	import { session } from '$lib/shell/session.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import type { components } from '$lib/api/schema';

	type User = components['schemas']['UserResponse'];

	let users = $state<User[]>([]);
	let loadFailed = $state(false);

	let newUsername = $state('');
	let newPassword = $state('');
	/* Typed twice, and that is not ceremony. An admin types this password blind and then says it
	 * out loud to somebody else. */
	let newPasswordAgain = $state('');
	let creating = $state(false);
	let nameError = $state<string | undefined>(undefined);
	let passwordError = $state<string | undefined>(undefined);

	/** Which user a row-level request is in flight for, so two clicks cannot race. */
	let busy = $state<string | null>(null);

	/** The user the reset form is open on, and what is typed into it. */
	let resetting = $state<User | null>(null);
	let resetPassword = $state('');
	let resetPasswordAgain = $state('');
	let resetError = $state<string | undefined>(undefined);

	/* Who is being removed, and whether the question is on screen: two things rather than one. */
	let removing = $state<User | null>(null);
	let removeOpen = $state(false);

	/* The one-click guest, and the password it was given. */
	let generated = $state<{ user: User; password: string } | null>(null);
	let generating = $state(false);

	/** Which user is being renamed, and to what. */
	let renaming = $state<User | null>(null);
	let newName = $state('');
	let renameError = $state<string | undefined>(undefined);

	async function generate() {
		if (generating) return;
		generating = true;
		try {
			generated =
				await api.post<components['schemas']['GeneratedUserResponse']>('/auth/users/generate');
			await load();
		} catch {
			toasts.show("Couldn't create a guest", { tone: 'error' });
		} finally {
			generating = false;
		}
	}

	async function copyPassword() {
		if (!generated) return;
		// Through the app's own helper rather than the browser clipboard API, which is absent on a
		// plain-http origin, which is what Sift is on a home network.
		const copied = await copyText(generated.password);
		toasts.show(copied ? 'Password copied' : "Couldn't copy the password. Write it down instead.", {
			tone: copied ? 'success' : 'error'
		});
	}

	async function rename(event: SubmitEvent) {
		event.preventDefault();
		const user = renaming;
		if (!user) return;
		renameError = undefined;
		busy = user.id;
		try {
			await api.post<User>(`/auth/users/${user.id}/username`, {
				body: { username: newName }
			});
			renaming = null;
			await load();
			// Their own name is on screen in the session menu, so a rename of yourself has to be
			// re-read rather than left showing the old one until the next reload.
			if (user.id === session.viewer?.id) await session.load();
			toasts.show('Renamed', { tone: 'success' });
		} catch (error) {
			/* The server's own words, not a sentence written here. */
			if (error instanceof ApiError && error.status === 409)
				renameError = error.detail ?? "That name can't be used.";
			else renameError = "That couldn't be saved.";
		} finally {
			busy = null;
		}
	}

	const guests = $derived(users.filter((user) => user.role === 'guest'));

	/* Everything that can be done to a guest's user, declared once. */
	function userVerbs(user: User): Verb[] {
		return [
			{
				id: 'signin',
				label: user.disabled ? 'Turn on sign-in' : 'Turn off sign-in',
				icon: user.disabled ? 'check' : 'block',
				run: () => void setDisabled(user, !user.disabled)
			},
			{
				id: 'rename',
				label: 'Rename',
				icon: 'edit_square',
				run: () => {
					renaming = user;
					newName = user.username;
					renameError = undefined;
				}
			},
			{
				id: 'reset',
				label: 'Reset password',
				icon: 'lock',
				run: () => {
					resetting = user;
					resetPassword = '';
					resetError = undefined;
				}
			},
			{
				id: 'remove',
				label: 'Delete',
				icon: 'delete',
				filled: true,
				destructive: true,
				run: () => {
					removing = user;
					removeOpen = true;
				}
			}
		];
	}

	onMount(() => {
		void load();
	});
	/* A user made, turned off, removed or given a new role in another window or by another admin
	   is said on the settings bell (the auth service's `_say`); the list follows it. */
	whenChanged(settingChanges, () => void load());

	async function load() {
		try {
			users = await api.get<User[]>('/auth/users');
			loadFailed = false;
		} catch {
			loadFailed = true;
		}
	}

	async function create(event: SubmitEvent) {
		event.preventDefault();
		if (creating) return;
		nameError = undefined;
		passwordError = undefined;
		if (newPassword !== newPasswordAgain) {
			passwordError = "The two passwords don't match.";
			return;
		}
		creating = true;
		try {
			await api.post<User>('/auth/users', {
				body: { username: newUsername, password: newPassword }
			});
			newUsername = '';
			newPassword = '';
			newPasswordAgain = '';
			await load();
			toasts.show('Guest created', { tone: 'success' });
		} catch (error) {
			// The conflict is the one refusal worth saying plainly.
			/* The server's own words, not a sentence written here. */
			if (error instanceof ApiError && error.status === 409)
				nameError = error.detail ?? "That name can't be used.";
			else if (error instanceof ApiError && error.status === 422)
				passwordError = error.detail ?? "That password doesn't meet the requirements.";
			else toasts.show("That couldn't be saved", { tone: 'error' });
		} finally {
			creating = false;
		}
	}

	async function setDisabled(user: User, disabled: boolean) {
		busy = user.id;
		try {
			await api.put<User>(`/auth/users/${user.id}/disabled`, { body: { disabled } });
			await load();
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		} finally {
			busy = null;
		}
	}

	async function resetTheirPassword(event: SubmitEvent) {
		event.preventDefault();
		const user = resetting;
		if (!user) return;
		resetError = undefined;
		if (resetPassword !== resetPasswordAgain) {
			resetError = "The two passwords don't match.";
			return;
		}
		busy = user.id;
		try {
			await api.post<void>(`/auth/users/${user.id}/password`, {
				body: { new_password: resetPassword }
			});
			resetting = null;
			resetPassword = '';
			resetPasswordAgain = '';
			toasts.show(`${user.username} has been signed out and needs the new password`, {
				tone: 'success'
			});
		} catch (error) {
			if (error instanceof ApiError && error.status === 422)
				resetError = error.detail ?? "That password doesn't meet the requirements.";
			else resetError = "That couldn't be saved.";
		} finally {
			busy = null;
		}
	}

	async function remove(user: User) {
		busy = user.id;
		try {
			await api.del<void>(`/auth/users/${user.id}`);
			await load();
			toasts.show(`${user.username} was deleted`, { tone: 'success' });
		} catch {
			toasts.show(`Couldn't delete ${user.username}`, { tone: 'error' });
		} finally {
			busy = null;
		}
	}
</script>

<section>
	<header>
		<p class="lede">
			Guests see nothing at all until you share something with them. Add a guest here, then use
			Share on a file, folder, person, tag or collection to decide what they can reach.
		</p>
	</header>

	{#if loadFailed}
		<Problem message="These users couldn't be loaded. Reload the page to try again." />
	{:else}
		<SettingGroup
			heading="New guest"
			help="You choose their first password and tell them what it is. Sift sends no email or invitation. They can change it themselves later."
		>
			<FormCard onsubmit={create}>
				<FieldRow label="Username" error={nameError}>
					{#snippet control({ id, describedBy, invalid })}
						<TextInput
							{id}
							{describedBy}
							{invalid}
							type="text"
							autocomplete="off"
							bind:value={newUsername}
						/>
					{/snippet}
				</FieldRow>

				<FieldRow label="First password" error={passwordError}>
					{#snippet control({ id, describedBy, invalid })}
						<PasswordInput {id} {describedBy} {invalid} bind:value={newPassword} />
					{/snippet}
					{#snippet under()}
						<PasswordStrength password={newPassword} />
					{/snippet}
				</FieldRow>

				<FieldRow label="First password again">
					{#snippet control({ id, describedBy })}
						<PasswordInput {id} {describedBy} bind:value={newPasswordAgain} />
					{/snippet}
					{#snippet press()}
						<Button
							type="submit"
							tone="primary"
							icon="add"
							disabled={creating ||
								!newUsername ||
								!assess(newPassword).acceptable ||
								!newPasswordAgain}
						>
							{creating ? 'Creating\u2026' : 'Create guest'}
						</Button>
					{/snippet}
				</FieldRow>
			</FormCard>

			<ActionRow
				id="users.invent"
				label={RANDOM_GUEST.name}
				help={RANDOM_GUEST.help}
				action={generating ? 'Creating\u2026' : 'Create'}
				busy={generating}
				onclick={() => void generate()}
			/>

			{#if generated}
				<div class="generated">
					<p class="username">{generated.user.username}</p>
					<p class="password">{generated.password}</p>
					<p class="note">
						This password appears only now. If it's lost, reset their password below.
					</p>
					<span class="actions">
						<Button tone="ghost" icon="content_copy" onclick={() => void copyPassword()}
							>Copy password</Button
						>
						<Button tone="ghost" onclick={() => (generated = null)}>Done</Button>
					</span>
				</div>
			{/if}
		</SettingGroup>

		<SettingGroup id="users.guests" heading={GUESTS.name}>
			{#if guests.length === 0}
				<p class="note">No guests yet. Only you can see this library.</p>
			{:else}
				<!-- The shared row, not a hand-written one: `DataRow` opens the same declared
				     list of verbs from the three-dot menu and from a right-click, so the two
				     cannot come to mean different things, and it holds the list still while a
				     pointer is inside it. -->
				<DataRows items={guests} key={(user: User) => user.id} label="Guests" edges>
					{#snippet row(user: User)}
						<DataRow verbs={userVerbs(user)} ids={[user.id]} menuLabel="More for {user.username}">
							<span class="who" class:off={user.disabled}>
								<span class="name">{user.username}</span>
								<span class="state">{user.disabled ? 'Sign-in turned off' : 'Can sign in'}</span>
							</span>
						</DataRow>
					{/snippet}
				</DataRows>

				<p class="note">
					Turning off someone's sign-in also signs them out of every browser right away.
				</p>
			{/if}
		</SettingGroup>

		{#if renaming}
			<SettingGroup
				heading="Rename {renaming.username}"
				help="Only the username changes. They stay signed in and see the same things."
			>
				<FormCard onsubmit={rename}>
					<FieldRow label="Username" error={renameError}>
						{#snippet control({ id, describedBy, invalid })}
							<TextInput
								{id}
								{describedBy}
								{invalid}
								type="text"
								autocomplete="off"
								bind:value={newName}
							/>
						{/snippet}
						{#snippet press()}
							<Button tone="ghost" onclick={() => (renaming = null)}>Cancel</Button>
							<Button type="submit" tone="primary" icon="edit_square" disabled={!newName}
								>Rename</Button
							>
						{/snippet}
					</FieldRow>
				</FormCard>
			</SettingGroup>
		{/if}

		{#if resetting}
			<SettingGroup
				heading="New password for {resetting.username}"
				help="You don't need their old password. They are signed out everywhere and need the new one to sign in again."
			>
				<FormCard onsubmit={resetTheirPassword}>
					<FieldRow label="New password" error={resetError}>
						{#snippet control({ id, describedBy, invalid })}
							<PasswordInput {id} {describedBy} {invalid} bind:value={resetPassword} />
						{/snippet}
						{#snippet under()}
							<PasswordStrength password={resetPassword} />
						{/snippet}
					</FieldRow>

					<FieldRow label="New password again">
						{#snippet control({ id, describedBy })}
							<PasswordInput {id} {describedBy} bind:value={resetPasswordAgain} />
						{/snippet}
						{#snippet press()}
							<Button
								tone="ghost"
								onclick={() => {
									resetting = null;
									resetPasswordAgain = '';
								}}>Cancel</Button
							>
							<Button type="submit" tone="primary" disabled={!resetPassword || !resetPasswordAgain}>
								Reset password
							</Button>
						{/snippet}
					</FieldRow>
				</FormCard>
			</SettingGroup>
		{/if}

		<SettingGroup heading="Your account">
			<FactRow
				label="Signed in as"
				help="Change your own password in Profile. If you lose it, you must run a recovery command on this device, because there's no reset email."
				fact={session.viewer?.username ?? ''}
			/>
		</SettingGroup>
	{/if}
</section>

<ConfirmDialog
	bind:open={removeOpen}
	title={removing ? `Delete ${removing.username}?` : 'Delete this guest?'}
	consequence="Their sign-in, their sessions and everything shared with them are deleted. Your files aren't touched."
	confirmLabel="Delete guest"
	onconfirm={() => {
		if (removing) void remove(removing);
	}}
/>

<style>
	/* The invented credentials, set apart from the form above them: this is something to read
	   and copy rather than something to fill in, and it is on screen once. */
	.generated {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		padding: var(--space-4);
		border-radius: var(--radius-md);
		border: 1px solid var(--sift-line);
		background: var(--sift-surface-2);
	}

	/* The new guest's username: a value to read out, not a heading over the card: the group's
	   heading above already says what this is. */
	.username {
		margin: 0;
		font: var(--text-body);
		font-weight: 600;
		color: var(--sift-ink);
	}

	/* Selectable in one gesture, with tabular figures. */
	.password {
		margin: 0;
		font: var(--text-body);
		font-variant-numeric: tabular-nums;
		letter-spacing: 0.04em;
		color: var(--sift-ink);
		user-select: all;
		word-break: break-all;
	}

	/* Ordinary block flow rather than a gapped column: the space each group leaves under itself
	   then collapses with the room above the next group's heading, as it does on every pane. */
	.lede,
	.note {
		margin: var(--space-2) 0 0;
		font: var(--text-body);
		color: var(--sift-ink-2);
	}

	.lede {
		margin-block-end: var(--space-6);
	}

	.note {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* The row's one control, dressed. */
	/* A blocked user is dimmed rather than hidden. Somebody who blocked a guest last month and
	   is wondering why they cannot get in needs to find the row, not lose it. */
	.who.off .name {
		color: var(--sift-ink-3);
	}

	.who {
		display: flex;
		flex-direction: column;
		gap: 2px;
		min-inline-size: 0;
	}

	.name {
		font: var(--text-body);
		color: var(--sift-ink);
	}

	.state {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.actions {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		flex: none;
	}
</style>
