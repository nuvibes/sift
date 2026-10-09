<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'SuggestInput',
		category: 'control',
		role: 'a text box that suggests while you type and lets what you typed win',
		basis: 'bits-ui:Combobox',
		states: ['empty', 'suggesting', 'typed']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* A box completing from the library's own vocabulary (the search suggester, as the field declares),
	 * never refusing what was typed: on the library's Combobox, the input is the source of truth,
	 * and
	 * Enter with nothing highlighted submits what was typed. */
	import { onMount } from 'svelte';
	import { Combobox } from 'bits-ui';
	import { suggestionsFor } from '$lib/search/search.svelte';
	import Scroller from './Scroller.svelte';

	interface Props {
		id?: string;
		describedBy?: string;
		value: string;
		/** The vocabulary to complete from: the query language's own token for it. */
		suggests: string;
		placeholder?: string;
		ariaLabel?: string;
		/** What the field wrapper says about the value, so the box reports it like a plain one. */
		invalid?: boolean;
		/** Take the caret on mount. For a box that IS the sheet somebody just opened. */
		takeFocus?: boolean;
		oninput: (typed: string) => void;
		/** Enter, or a suggestion picked. The caller decides what that means. */
		onsubmit?: (typed: string) => void;
		/** Names never offered, whatever is typed: a record's own name in a field naming another. */
		leaveOut?: readonly string[];
	}

	let {
		id,
		describedBy,
		value,
		suggests,
		placeholder,
		ariaLabel,
		invalid = false,
		takeFocus = false,
		oninput,
		onsubmit,
		leaveOut = []
	}: Props = $props();

	let offered = $state<string[]>([]);
	let open = $state(false);
	let box = $state<HTMLInputElement | null>(null);
	/* Whether the arrows were used since opening, so Enter never takes an unreached first row. */
	let navigated = $state(false);

	/* One request per pause, a generation counter dropping stale answers. */
	let generation = 0;
	let timer: ReturnType<typeof setTimeout> | undefined;
	const DEBOUNCE_MS = 120;

	function ask(typed: string) {
		clearTimeout(timer);
		const mine = ++generation;
		const wanted = typed.trim();
		if (wanted.length < 2) {
			dismiss();
			return;
		}
		timer = setTimeout(async () => {
			try {
				const found = await suggestionsFor(suggests, wanted);
				if (mine !== generation) return;
				// Exactly what is typed adds nothing.
				const never = new Set(leaveOut.map((one) => one.trim().toLowerCase()));
				offered = found
					.map((one) => one.value)
					.filter((one) => one.toLowerCase() !== wanted.toLowerCase())
					.filter((one) => !never.has(one.trim().toLowerCase()))
					.slice(0, 8);
				open = offered.length > 0;
			} catch {
				// No completions, never an error: everything can be typed by hand.
				if (mine === generation) {
					dismiss();
				}
			}
		}, DEBOUNCE_MS);
	}

	/* Every way the list goes, in one place, clearing `navigated` with it. */
	function dismiss() {
		open = false;
		offered = [];
		navigated = false;
	}

	/* The row the arrows reached, by `aria-activedescendant`, only from this box's own list. */
	function highlighted(): string | null {
		const named = box?.getAttribute('aria-activedescendant');
		if (!named) return null;
		const row = box?.ownerDocument.getElementById(named);
		return row?.getAttribute('data-value') ?? null;
	}

	function take(one: string) {
		dismiss();
		oninput(one);
		onsubmit?.(one);
		box?.focus();
	}

	onMount(() => {
		if (takeFocus) box?.focus();
		return () => clearTimeout(timer);
	});
</script>

<Combobox.Root
	type="single"
	bind:open
	onOpenChange={(showing: boolean) => {
		// A closed list is an empty one: the rows are drawn only while there is a list, so Escape
		// and a press outside take the rows away rather than hiding a list that is still there,
		// and they take the arrows' memory with them, whoever closed it.
		if (!showing) dismiss();
	}}
	inputValue={value}
	onValueChange={(picked: string) => {
		if (picked) take(picked);
	}}
>
	<Combobox.Input
		bind:ref={box}
		{id}
		class="ui-combobox-input"
		{placeholder}
		aria-label={ariaLabel}
		aria-describedby={describedBy}
		aria-invalid={invalid || undefined}
		autocomplete="off"
		oninput={(event: Event) => {
			const typed = (event.currentTarget as HTMLInputElement).value;
			navigated = false;
			oninput(typed);
			ask(typed);
		}}
		onkeydown={(event: KeyboardEvent) => {
			if (event.key === 'Escape' && open) {
				// Swallowed only while the list is up, and closed here rather than by the library's
				// document listener, which the swallowing keeps the key from: a window-level handler
				// above this closes the whole sheet, and dismissing a completion list should never
				// close the form under it.
				event.stopPropagation();
				dismiss();
				return;
			}
			if (event.key === 'ArrowDown' || event.key === 'ArrowUp') navigated = true;
			/*
			 * Enter is this box's, arrows or no arrows.
			 *
			 * After ArrowDown, Enter puts the row's text in the box and closes the list without the
			 * library firing `onValueChange`, so relying on that would add nothing and leave
			 * `navigated` true with no list under it, letting the next Enter submit the whole
			 * record. So the row is read off the highlight the library publishes to assistive
			 * technology and taken here, the default is prevented either way, and every close goes
			 * through `dismiss`.
			 */
			if (event.key === 'Enter') {
				// An Enter that commits an IME composition is the end of a word, not an answer.
				if (event.isComposing) return;
				event.preventDefault();
				const row = navigated && open ? highlighted() : null;
				if (row !== null) {
					take(row);
					return;
				}
				dismiss();
				onsubmit?.(value);
			}
		}}
	/>

	{#if offered.length > 0}
		<Combobox.Portal>
			<Combobox.Content class="ui-combobox-content" sideOffset={4}>
				<!-- The shared scroller, whose bar floats. -->
				<Scroller>
					<Combobox.Viewport>
						{#each offered as one, at (`${at}:${one}`)}
							<Combobox.Item value={one} label={one} class="ui-combobox-item">{one}</Combobox.Item>
						{/each}
					</Combobox.Viewport>
				</Scroller>
			</Combobox.Content>
		</Combobox.Portal>
	{/if}
</Combobox.Root>

<style>
	/* The library's combobox dressed here; global, as classes this file writes. */
	:global(.ui-combobox-input) {
		padding: var(--space-1) var(--space-2);
		border: 1px solid var(--sift-line-strong);
		border-radius: var(--radius-sm);
		background: var(--sift-surface-3);
		color: var(--sift-ink);
		/* The body face, not a size typed here: the box types what the rows read. */
		font: var(--text-body);
		transition: background-color var(--dur-instant) var(--ease);
	}

	/* The hover layer every text box takes (see `--layer-hover`), and not while it is typed in. */
	:global(.ui-combobox-input:hover:not(:focus, :disabled)) {
		background-color: color-mix(in srgb, currentColor var(--layer-hover), var(--sift-surface-3));
	}

	:global(.ui-combobox-input:focus-visible) {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	/* The open list on the menu surface; it clips, the scroller inside scrolls. */
	:global(.ui-combobox-content) {
		z-index: var(--z-menu);
		min-inline-size: var(--bits-combobox-anchor-width);
		overflow: hidden;
		/* The app's ceiling for a floating list, and the smaller of it and the room the window has. */
		max-block-size: min(
			var(--bits-combobox-content-available-height, var(--menu-max-height)),
			var(--menu-max-height)
		);
		padding: var(--space-1);
		border: 1px solid var(--sift-line);
		/* The menu corner, so the rows' corners are concentric. */
		border-radius: var(--radius-lg);
		background: var(--sift-surface-3);
		box-shadow: var(--elev-2);
	}

	/* Inset and cornered like every row opening over the page. */
	:global(.ui-combobox-item) {
		display: flex;
		align-items: center;
		padding: var(--menu-row-padding);
		border-radius: var(--menu-row-radius);
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
		cursor: pointer;
		/* The ground steps rather than snapping. */
		transition: background var(--dur-instant) var(--ease);
	}

	/* One highlight for keyboard and pointer. */
	:global(.ui-combobox-item[data-highlighted]) {
		background: var(--menu-row-highlight);
		color: var(--sift-ink);
	}
</style>
