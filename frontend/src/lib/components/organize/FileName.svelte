<script lang="ts" module>
	/*
	 * A filename cut into the runs it may wrap between.
	 *
	 * A filename has no spaces to wrap at, so a narrow tile would break it wherever the line ran
	 * out, in the middle of a word. It breaks instead after a dot, an underscore or a hyphen, and between a
	 * lower-case letter and the capital that starts the next word, which is where a person reading
	 * the name sees its parts. A run still too long for the line breaks where it must.
	 */
	export function nameRuns(name: string): string[] {
		return name.split(/(?<=[._-])|(?<=[a-z])(?=[A-Z])/).filter((run) => run.length > 0);
	}
</script>

<script lang="ts">
	interface Props {
		/** The filename exactly as it is stored. */
		name: string;
	}

	let { name }: Props = $props();

	const runs = $derived(nameRuns(name));
</script>

<!-- No whitespace between the tags: any would land inside the name, as text. `<wbr>` adds a place
     to wrap and nothing to the text, so a name copied by hand is the name. -->
{#each runs as run, index (index)}{#if index > 0}<wbr />{/if}{run}{/each}
