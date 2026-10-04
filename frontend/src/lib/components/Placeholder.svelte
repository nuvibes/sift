<script lang="ts">
	interface Props {
		title: string;
	}

	let { title }: Props = $props();

	// Every route in the app resolves from the day the shell exists, even where the screen behind it
	// has not been built. That is not politeness: a route that does not exist yet is a route somebody
	// invents a second time, in a different shape, and then two of them have to be reconciled.
</script>

<h1>{title}</h1>
<p>Nothing here yet.</p>

<style>
	h1 {
		font: var(--text-display);
		letter-spacing: var(--tracking-display);
		/* A name in a heading can be one unbroken token; it breaks rather than leaving the box. */
		overflow-wrap: anywhere;
	}

	p {
		color: var(--muted-foreground);
		font: var(--text-body);
	}
</style>
