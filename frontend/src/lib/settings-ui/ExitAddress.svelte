<script lang="ts">
	/* The address of a machine, shown a little at a time.
	 *
	 * "Which server am I actually on" is the question somebody asks of a tunnel they are relying
	 * on, and it is the one thing that tells a working tunnel from a working tunnel to the wrong
	 * country. But it is also the address of a machine, sitting on a screen that stays open, so
	 * the whole of it is not something to leave lying in front of whoever walks past.
	 *
	 * So the first part is plain and the rest is covered until it is asked for. The cover is a
	 * screen measure and not a secret: the address is in the page either way, and this is
	 * admin-only like everything else about downloading. What it stops is somebody reading it
	 * over a shoulder.
	 *
	 * ## Covered, never blurred
	 *
	 * The rest is painted OVER (a filled box the width of the text) rather than blurred: the shared
	 * `Covered`, which says why, and which a file's place draws its hidden profile folder with too,
	 * so a hidden part reads the same wherever it is.
	 *
	 * ## Two callers, and the second is the one you are MEANT to read
	 *
	 * The tunnel rows, and the address Sift shares its library on (Privacy > Network sharing).
	 * The second one has a Copy button beside it, and that is what makes covering it reasonable
	 * rather than obstructive: the way to use that address is to copy it into the other computer,
	 * which never needs it on the screen. Somebody who does want to read it presses once.
	 */
	import Covered from '$lib/components/common/Covered.svelte';
	import Toggle from '$lib/components/common/Toggle.svelte';

	interface Props {
		address: string;
		/**
		 * A short phrase naming this address, which the control's label is built from: "the address
		 * Netherlands is connected to", "this computer's address".
		 *
		 * A PHRASE rather than a bare name, because the two callers need different sentences and a
		 * name alone cannot make both. A tunnel's label has to say what the address is FOR (the
		 * tunnel's own name is not an address), and the sharing pane's address belongs to nothing
		 * but the machine it is read on.
		 */
		what: string;
	}

	let { address, what }: Props = $props();

	/* A pressed-or-not control, so it is the library's Toggle rather than a button with an
	   `aria-pressed` written on by hand. The state is entirely this screen's (nothing is saved and
	   no request is made), which is what makes the toggle the right shape here: it may own the
	   answer, because there is no other holder of it to disagree with. */
	let shown = $state(false);

	/* A scheme is never the secret, so it is never covered. `http://` in front of a masked address
	   reads as an address; a masked `http:` reads as a rendering fault, and it is also the one part
	   of the string that says nothing about which machine this is. */
	const scheme = $derived(address.match(/^[a-z][a-z0-9+.-]*:\/\//i)?.[0] ?? '');
	const bare = $derived(address.slice(scheme.length));

	/*
	 * The first part plain, the rest hidden. Dots for IPv4 and for a name, colons for IPv6.
	 *
	 * DOTS WIN WHEN THERE ARE ANY. A colon first is right for a bare v6 address and wrong for every
	 * v4 address with a PORT on it: `10.0.0.4:5171` would split into `10.0.0.4` and `5171`, leaving
	 * the whole machine address on the screen and covering the port, which identifies nothing. A v6
	 * address has no dots in it, so reaching for the colon only when there are none is right in
	 * both cases rather than in one.
	 */
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

	/* Underlined as well as stepped. There is no ground here on purpose (it is an address you
	   press to reveal, sitting in a line of prose), so the mark a bare word takes is the
	   underline, which is the same answer the shared button's `quiet` tone gives for the same
	   reason. */
	.exit :global(.address:hover) {
		color: var(--sift-ink-2);
		text-decoration: underline;
	}

	.exit :global(.address:focus-visible) {
		border-radius: var(--radius-sm);
	}
</style>
