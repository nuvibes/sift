<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'PasswordInput',
		category: 'control',
		role: 'a password typed with a way to see it',
		basis: 'site:<input type=password>',
		states: ['hidden', 'shown']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: bits-ui has no password field: PinInput is a different control for a different job. The
	box is the site's <input>; what is added is the reveal, which is a button beside it. */
	/* A password box with a reveal: no password in Sift can be recovered, so a typo matters. The
	 * reveal starts off, never persists, and names the state it moves to. */
	import Icon from '$lib/components/Icon.svelte';

	interface Props {
		value: string;
		id?: string;
		describedBy?: string;
		invalid?: boolean;
		/** `new-password` on anything being chosen, `current-password` on a sign-in. */
		autocomplete?: 'new-password' | 'current-password' | 'off';
		placeholder?: string;
		name?: string;
	}

	let {
		value = $bindable(''),
		id,
		describedBy,
		invalid,
		autocomplete = 'new-password',
		placeholder,
		name
	}: Props = $props();

	let revealed = $state(false);
</script>

<div class="password">
	<!-- `type` set by hand: Svelte refuses `bind:value` with a dynamic type. -->
	<input
		{id}
		{name}
		{placeholder}
		{autocomplete}
		type={revealed ? 'text' : 'password'}
		aria-describedby={describedBy}
		aria-invalid={invalid ? 'true' : undefined}
		bind:value
	/>
	<button
		type="button"
		class="reveal"
		aria-pressed={revealed}
		onclick={() => (revealed = !revealed)}
	>
		<Icon
			name={revealed ? 'visibility_off' : 'visibility'}
			size={16}
			label={revealed ? 'Hide password' : 'Show password'}
		/>
	</button>
</div>

<style>
	.password {
		position: relative;
		display: block;
	}

	/* The box dresses itself as Field would, so it is right outside one too. */
	input:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	.reveal {
		position: absolute;
		inset-block: 0;
		inset-inline-end: 0;
		display: grid;
		place-items: center;
		inline-size: var(--space-8);
		border: 0;
		border-radius: var(--radius-md);
		background: none;
		color: var(--sift-ink-3);
		cursor: pointer;
		transition:
			transform var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease);
	}

	/* It grows rather than gaining a ground. */
	/* A finger's width on a phone for the reveal. */
	@media (max-width: 767px) {
		.reveal {
			inline-size: var(--touch-target);
		}

		input {
			padding-inline-end: var(--touch-target);
		}
	}

	/* The typed characters stop where the reveal begins, rather than running on under it. */
	input {
		padding-inline-end: var(--space-8);
	}

	.reveal:hover,
	.reveal[aria-pressed='true'] {
		transform: scale(1.18);
		color: var(--sift-ink);
	}
</style>
