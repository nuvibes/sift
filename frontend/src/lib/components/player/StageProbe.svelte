<script lang="ts">
	/* WHY NOT SHARED: button: this is a test fixture, not interface. Nothing here is ever drawn for anybody, so
	   there is no look to keep consistent with anything: the element exists to be clicked by a
	   test and to print what the stage says. */
	/*
	 * A stand-in for the player, for testing the stage. Never rendered by the application.
	 *
	 * It has to be its own component rather than a snippet in the test or in the harness. Context
	 * follows the render tree: a snippet is compiled in the scope of the file that WROTE it, so one
	 * written outside the stage reads no context at all, however deep inside the stage it is drawn.
	 * A component instantiated inside the stage's own rendering is a child of it and does.
	 */
	import { getStage } from './stage.svelte';

	const frame = getStage();
</script>

<button type="button" class="probe-fullscreen" onclick={() => frame?.toggleFullscreen()}>
	{frame?.isFullscreen ? 'leave' : 'enter'}
</button>

<span class="probe-has-frame">{frame ? 'yes' : 'no'}</span>
