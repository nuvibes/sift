/* The offer of where downloads go, on the Folders screen in Settings. */
import { expect, it } from 'vitest';

import source from './LibraryScreen.svelte?raw';

const markup = source.slice(source.indexOf('</script>'));

it('mounts the download folder offer before the loading branch, not inside it', () => {
	const offer = markup.indexOf('<DownloadFolderOffer {library} />');
	const branch = markup.indexOf('{#if library.failed}');
	expect(offer).toBeGreaterThan(-1);
	expect(branch).toBeGreaterThan(offer);
	expect(markup.split('<DownloadFolderOffer').length).toBe(2);
});
