<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'ColorPicker',
		category: 'control',
		role: 'a color chosen by eye: a square of one hue, a slider through the hues, and the hex it comes to',
		basis: 'composes:Slider,TextInput,Button',
		states: ['default', 'dragging', 'refused']
	} satisfies DesignEntry;

	/** A colour the chosen one turns into, drawn in the row under the box. See `swatches`. */
	export interface DerivedSwatch {
		/** What the role is called, in a word or two. */
		name: string;
		colour: string;
	}
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: the library has no colour control, and the parts that HAVE behaviour worth
	borrowing are already primitives here (Slider, TextInput, Button); the square is two numbers. */

	/* Choosing a colour in Sift's own chrome, never `<input type="color">`. `oninput` on every move,
	 * `onchange` when it ends; the three numbers are held, since a grey has no hue to read back. */
	import Button from './Button.svelte';
	import Tooltip from './Tooltip.svelte';
	import Slider from './Slider.svelte';
	import TextInput from './TextInput.svelte';
	import { copyText } from '$lib/shell/clipboard';
	import { hueColour, toHex, toHsv } from './hsv';

	interface Props {
		/** What the colour is for, read to a screen reader: "Accent colour". */
		label: string;
		/** The colour in force, `#rrggbb`. */
		value: string;
		/** Moved. Called on every step of a drag, with `#rrggbb`. For painting, never for storing. */
		oninput: (colour: string) => void;
		/** Settled: a drag let go of, an arrow key, a hex typed and left. This is the one to store. */
		onchange: (colour: string) => void;
		/** What this colour turns into (fill, text, tint), shown under the box. */
		swatches?: DerivedSwatch[];
	}

	let { label, value, oninput, onchange, swatches = [] }: Props = $props();

	/* One arrow press moves a twentieth of the square. */
	const KEY_STEP = 0.05;

	/** The copy tick's two seconds, as the Updates screen's. */
	const COPIED_FOR = 2000;

	let hue = $state(0);
	let saturation = $state(0);
	let brightness = $state(0);

	/** The square, so a pointer's position can be measured against it. */
	let square = $state<HTMLElement | null>(null);

	/** What is in the hex box. Follows the colour in force until somebody types over it. */
	let typed = $state('');
	let refused = $state(false);
	let copied = $state(false);

	/** The error line's id, unique per instance. */
	const errorId = $props.id();

	/** The colour the three numbers come to. */
	const chosen = $derived(toHex({ hue, saturation, value: brightness }));

	/* Take a colour set elsewhere, keeping the hue when it cannot name one. */
	$effect(() => {
		const parsed = toHsv(value);
		typed = value;
		refused = false;
		if (parsed === null) return;
		saturation = parsed.saturation;
		brightness = parsed.value;
		if (parsed.saturation > 0 && parsed.value > 0) hue = parsed.hue;
	});

	/** Say the colour and put it in the box, which a drag does not move through the prop. */
	function say(settled: boolean): void {
		const colour = toHex({ hue, saturation, value: brightness });
		typed = colour;
		refused = false;
		oninput(colour);
		if (settled) onchange(colour);
	}

	/** Where in the square a point is, as saturation across and brightness down. */
	function aimAt(event: PointerEvent): void {
		if (square === null) return;
		const box = square.getBoundingClientRect();
		if (box.width === 0 || box.height === 0) return;
		saturation = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
		brightness = 1 - Math.min(1, Math.max(0, (event.clientY - box.top) / box.height));
	}

	/* The drag, with the pointer captured, so straying off the square neither closes the popover
	   nor stops: past the edge is full saturation. */
	function grab(event: PointerEvent): void {
		square?.setPointerCapture(event.pointerId);
		aimAt(event);
		say(false);
	}

	function drag(event: PointerEvent): void {
		if (square === null || !square.hasPointerCapture(event.pointerId)) return;
		aimAt(event);
		say(false);
	}

	function letGo(event: PointerEvent): void {
		if (square === null || !square.hasPointerCapture(event.pointerId)) return;
		square.releasePointerCapture(event.pointerId);
		say(true);
	}

	/* The arrows are the square's keyboard; settled on every press. */
	function byKey(event: KeyboardEvent): void {
		const across = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
		const down = event.key === 'ArrowUp' ? 1 : event.key === 'ArrowDown' ? -1 : 0;
		if (across === 0 && down === 0) return;
		event.preventDefault();
		saturation = Math.min(1, Math.max(0, saturation + across * KEY_STEP));
		brightness = Math.min(1, Math.max(0, brightness + down * KEY_STEP));
		say(true);
	}

	function slideHue(next: number, settled: boolean): void {
		hue = next;
		say(settled);
	}

	/* What to do about it; the colour in force stays. */
	const ERROR = 'Type a color as a hash and six digits, using 0 to 9 and a to f.';

	/** A hex typed and left, believed on `change`, never per keystroke. */
	function take(text: string): void {
		const parsed = toHsv(text.trim().toLowerCase());
		if (parsed === null) {
			refused = true;
			return;
		}
		saturation = parsed.saturation;
		brightness = parsed.value;
		if (parsed.saturation > 0 && parsed.value > 0) hue = parsed.hue;
		say(true);
	}

	async function copy(): Promise<void> {
		// Through the helper: `navigator.clipboard` is absent over plain http.
		if (!(await copyText(chosen))) return;
		copied = true;
		setTimeout(() => (copied = false), COPIED_FOR);
	}
</script>

<div class="picker">
	<!-- A real button, focusable; Space and Enter do nothing, the arrows are the keyboard. -->
	<button
		bind:this={square}
		type="button"
		class="square"
		style:--hue={hueColour(hue)}
		aria-label="{label}: saturation and brightness. Use the arrow keys, or type a color in the box below."
		onpointerdown={grab}
		onpointermove={drag}
		onpointerup={letGo}
		onkeydown={byKey}
	>
		<span
			class="marker"
			style:--across="{saturation * 100}%"
			style:--down="{(1 - brightness) * 100}%"
			style:background-color={chosen}
		></span>
	</button>

	<Slider
		label="{label}: hue"
		value={hue}
		max={359}
		ground="var(--hue-spectrum)"
		valueText="{Math.round(hue)} degrees"
		oninput={(next) => slideHue(next, false)}
		onchange={(next) => slideHue(next, true)}
	/>

	<div class="entry">
		<TextInput
			value={typed}
			invalid={refused}
			aria-label="{label}: the color as a hex code"
			describedBy={refused ? errorId : undefined}
			spellcheck={false}
			autocapitalize="off"
			autocomplete="off"
			oninput={(event) => (typed = (event.currentTarget as HTMLInputElement).value)}
			onchange={(event) => take((event.currentTarget as HTMLInputElement).value)}
		/>
		<!-- The tick replaces the glyph, so the row keeps its width. -->
		<Tooltip label={copied ? 'Copied' : 'Copy the color'} staysOnPress>
			<Button
				tone="ghost"
				icon={copied ? 'check' : 'content_copy'}
				aria-label={copied ? 'Copied' : 'Copy the color'}
				onclick={() => void copy()}
			/>
		</Tooltip>
	</div>

	{#if refused}
		<p class="refused" id={errorId} role="alert">{ERROR}</p>
	{/if}

	{#if swatches.length > 0}
		<!-- What the colour becomes, as a row of samples. -->
		<ul class="derived">
			{#each swatches as swatch (swatch.name)}
				<li class="derived-one">
					<span class="derived-dot" style:background-color={swatch.colour}></span>
					<span class="derived-name">{swatch.name}</span>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.picker {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	/*
	 * One hue washed white leftward and black downward, by tokens; `--hue` arrives through
	 * `style:`.
	 */
	.square {
		position: relative;
		inline-size: 100%;
		block-size: var(--colour-square);
		padding: 0;
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-md);
		background: var(--colour-square-wash), var(--hue);
		cursor: crosshair;
		/* No scroll from a touch that starts here. */
		touch-action: none;
	}

	.square:focus-visible {
		outline: none;
		border-color: transparent;
		box-shadow: var(--focus-ring);
	}

	/* The marker: two rings, light and dark, so one shows in every corner. */
	.marker {
		position: absolute;
		/* Declared, so a marker with no colour yet sits top left. */
		--down: 0%;
		--across: 0%;
		inset-block-start: var(--down);
		inset-inline-start: var(--across);
		inline-size: var(--colour-marker);
		block-size: var(--colour-marker);
		border: 2px solid var(--sift-ink);
		border-radius: var(--radius-full);
		outline: 1px solid var(--sift-bg);
		transform: translate(-50%, -50%);
		pointer-events: none;
	}

	/* The box and its copy button, at the row's end. */
	.entry {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.refused {
		margin: 0;
		color: var(--sift-bad-text);
		font: var(--text-body-sm);
	}

	.derived {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-3);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.derived-one {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
	}

	.derived-dot {
		inline-size: var(--space-3);
		block-size: var(--space-3);
		border: 1px solid var(--sift-line-strong);
		border-radius: var(--radius-full);
	}

	.derived-name {
		color: var(--sift-ink-3);
		font: var(--text-micro);
	}
</style>
