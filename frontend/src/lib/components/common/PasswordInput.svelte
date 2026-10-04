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
	/*
	 * A password box with a way to see what you typed.
	 *
	 * Every password in Sift is typed blind and none of them can be recovered: there is no reset
	 * email, and the console tool that does exist destroys the saved site logins on its way past. A
	 * typo in a box nobody can read is therefore not a minor annoyance; on the setup screen it is an
	 * install somebody has locked themselves out of ten seconds after making it.
	 *
	 * The reveal starts off and never persists. It is a button rather than a checkbox because it is
	 * an action rather than a preference, and it says which state it will move to rather than which
	 * state it is in: "Show password" while hidden, which is the thing the person wants.
	 */
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
	<!--
		`type` is set by hand rather than bound, because Svelte refuses a two-way `bind:value` on an
		input whose type is dynamic: it cannot know which value property to write. Setting the
		attribute and keeping the binding is the same thing to the browser and legal to the compiler.
	-->
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

	/*
	 * The box dresses itself: border, ground and ink, the same values `Field` gives an input, so it
	 * is correct outside a `Field` too rather than falling back to a native input. `Field`'s rule
	 * is more specific, so a password box inside one still takes the field's.
	 */
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

	/* It grows rather than gaining a ground.
	 *
	 * A colour change alone is refused, and rightly here: a control drawn inside a field has no
	 * edge of its own, so on a light frame a step in grey is very nearly nothing. A round ground behind it
	 * satisfies the rule and looks wrong: a grey disc under a small glyph reads as a smudge rather
	 * than as the control waking up. So the scale register: it animates, it is visible on any
	 * ground because it is not a colour, and it puts nothing behind a small mark on a field. */
	/* A finger's width on a phone: the box is a finger's height there, and the reveal is the one
	   press inside it, and the box keeps the same room for it at its end. */
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
