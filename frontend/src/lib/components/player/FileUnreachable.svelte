<script lang="ts">
	/*
	 * A file whose bytes Sift cannot reach, said the same way for a picture and a video.
	 *
	 * The row is still in the library (its facts are below the stage), so this says what is known
	 * and what the likely reasons are, never "not found": the file is found, its bytes are not.
	 * The glyph is the one the tile's gone mark wears, distinct from the hidden mark's crossed eye.
	 */
	import { Empty } from '$lib/components/common';
</script>

<Empty scope="page" icon="unknown_document" title="Sift can't reach this file">
	It's still in the library, and everything known about it is below. The file itself isn't where
	Sift last saw it. A drive may not be mounted, a folder may have moved, or an archive may have been
	deleted since Sift took it in.
</Empty>
