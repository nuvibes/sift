<script lang="ts">
	/* NOT ON THE GALLERY: it is drawn only while a session's saved keys are locked, a state the
	   gallery cannot produce without a restart. The `PasswordInput` and `Button` it is made of are
	   on the gallery. */

	/*
	 * The password field that unlocks the saved keys, cookies and tunnels, wherever it is offered.
	 *
	 * ONE FIELD, SEVERAL DOORS: `Settings > Sites and Tunnels` and the panes beside it, the bar across
	 * the top of every screen after a restart, and a task on Activity parked for the password. A door
	 * free to write its own box, its own request and its own refusal would, written differently, be
	 * the one that unlocked and left the others asking. So the box, the press and what a
	 * refusal says live here, and the act itself is `unlock` in `$lib/shell/unlock.svelte`.
	 *
	 * Two shapes for the two kinds of place. `row` is a settings pane's labelled row, the way every
	 * other field on that pane is drawn. `inline` is the box and its press on one line, for a bar or
	 * a row of a list that has no column of names to put a label in.
	 */
	import { onMount, type Snippet } from 'svelte';
	import { Button, FormCard, PasswordInput } from '$lib/components/common';
	import FieldRow from './FieldRow.svelte';
	import { REFUSED, unlock } from '$lib/shell/unlock.svelte';

	interface Props {
		/** A settings pane's labelled row, or the box and its press on one line. */
		layout?: 'row' | 'inline';
		/** Put the typing in the box immediately: the field was opened by a press that asked for it. */
		focus?: boolean;
		/** Told once the keys are unlocked, for a screen whose list moves when they are. */
		onunlocked?: () => void;
		/** A press beside Unlock on the inline line, the door's own: the bar's Not now. */
		beside?: Snippet;
	}

	let { layout = 'row', focus = false, onunlocked, beside }: Props = $props();

	const uid = $props.id();
	const boxId = `unlock-${uid}`;
	const errorId = `${boxId}-error`;

	let password = $state('');
	let busy = $state(false);
	let refused = $state<string | undefined>(undefined);
	let inline = $state<HTMLFormElement | undefined>(undefined);

	onMount(() => {
		if (focus) inline?.querySelector('input')?.focus();
	});

	async function send(event: SubmitEvent) {
		event.preventDefault();
		if (!password) return;
		busy = true;
		refused = undefined;
		const unlocked = await unlock.unlock(password);
		busy = false;
		if (!unlocked) {
			refused = REFUSED;
			return;
		}
		password = '';
		// Unlocking starts every tunnel that was switched on, on the server, with no request from
		// this screen to answer, so a list beside the field would go on showing the state from
		// before until somebody reloaded the page, which reads as the unlock not having worked.
		onunlocked?.();
	}
</script>

{#if layout === 'row'}
	<FormCard onsubmit={send}>
		<FieldRow label="Your password" error={refused}>
			{#snippet control({
				id,
				describedBy,
				invalid
			}: {
				id: string;
				describedBy: string | undefined;
				invalid: boolean;
			})}
				<!-- The shared password box, with its eye: every password in Sift can be shown as it
				     is typed. -->
				<PasswordInput
					{id}
					{invalid}
					bind:value={password}
					{describedBy}
					autocomplete="current-password"
				/>
			{/snippet}
			{#snippet press()}
				<Button tone="primary" type="submit" disabled={busy || !password}>Unlock</Button>
			{/snippet}
		</FieldRow>
	</FormCard>
{:else}
	<form class="inline" bind:this={inline} onsubmit={send}>
		<label class="name" for={boxId}>Your password</label>
		<span class="box">
			<PasswordInput
				id={boxId}
				invalid={refused !== undefined}
				bind:value={password}
				describedBy={refused ? errorId : undefined}
				autocomplete="current-password"
			/>
		</span>
		<span class="presses">
			<Button tone="primary" type="submit" disabled={busy || !password}>Unlock</Button>
			{#if beside}{@render beside()}{/if}
		</span>
		<!-- Announced when it appears, because the person who needs it may have already moved on. -->
		{#if refused}<p class="error" id={errorId} role="alert">{refused}</p>{/if}
	</form>
{/if}

<style>
	/* The box and its press on one line, the words naming the box before it. Wrapping rather than
	   squeezing: on a phone the refusal takes a line of its own under the box. */
	.inline {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
		margin: 0;
	}

	.name {
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}

	.box {
		flex: 0 1 auto;
		min-inline-size: 0;
	}

	/* At the end of the line, and at the end of their own line once the field wraps. */
	.presses {
		margin-inline-start: auto;
		display: flex;
		align-items: center;
		justify-content: var(--row-pack, flex-end);
		gap: var(--space-2);
	}

	/* A phone: the words, then the box across the whole line, then the presses at its end, rather
	   than a box squeezed between a label and a press with the next press wrapped on its own. */
	@media (max-width: 767px) {
		.inline {
			flex: 1 1 auto;
			flex-direction: column;
			align-items: stretch;
		}

		.box :global(input) {
			inline-size: 100%;
		}
	}

	.error {
		flex-basis: 100%;
		margin: 0;
		color: var(--sift-bad-text);
		font: var(--text-body-sm);
	}
</style>
