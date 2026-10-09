<script lang="ts">
	/* The address of a machine, shown a little at a time. */
	import Covered from '$lib/components/common/Covered.svelte';
	import Toggle from '$lib/components/common/Toggle.svelte';

	interface Props {
		address: string;
		/** A short phrase naming this address, which the control's label is built from: "the
		 * address Netherlands is connected to", "this computer's address". */
		what: string;
	}

	let { address, what }: Props = $props();

	/* A pressed-or-not control, so it is the library's Toggle rather than a button with an
	   `aria-pressed` written on by hand. */
	let shown = $state(false);

	/* A scheme is never the secret, so it is never covered. */
	const scheme = $derived(address.match(/^[a-z][a-z0-9+.-]*:\/\//i)?.[0] ?? '');
	const bare = $derived(address.slice(scheme.length));

	/* The first part plain, the rest hidden. Dots for IPv4 and for a name, colons for IPv6. */
	const joiner = $derived(bare.includes('.') ? '.' : ':');
	const parts = $derived(bare.split(joiner));
	const head = $derived(scheme + (parts[0] ?? bare));
	const rest = $derived(parts.slice(1).join(joiner));
</script>

{#if rest}
	<!-- The shared Toggle renders the button and takes this file's class, so the rules below reach it
	     through an anchored :global on the wrapper. -->
	<span class="exit">
		<Toggle
			bind:pressed={shown}
			class="address"
			label={shown ? `Hide the rest of ${what}` : `Show the whole of ${what}`}
		>
			<span>{head}{joiner}</span><Covered {shown}>{rest}</Covered>
		</Toggle>
	</span>
{:else}
	<span class="address plain">{address}</span>
{/if}

<style>
	.exit :global(.address),
	.address {
		color: var(--sift-ink-3);
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
		cursor: pointer;
		white-space: nowrap;
		/* The change steps over --dur-instant rather than happening between frames. */
		transition: color var(--dur-instant) var(--ease);
	}

	.plain {
		cursor: default;
	}

	/* Underlined as well as stepped. */
	.exit :global(.address:hover) {
		color: var(--sift-ink-2);
		text-decoration: underline;
	}

	.exit :global(.address:focus-visible) {
		border-radius: var(--radius-sm);
	}
</style>
