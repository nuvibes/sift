<script lang="ts">
	/* Says what a wall inside the hold is told before it takes the top bar. */
	import { wallHeld } from './TabLayer.svelte';

	const leaving = wallHeld();
</script>

<span class="probe" data-held={leaving()}></span>
