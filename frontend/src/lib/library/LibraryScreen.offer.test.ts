/*
 * The offer of where downloads go, on the Folders screen in Settings.
 *
 * A first folder can be added here as well as on Browse's empty wall, so the offer is mounted here
 * too, and OUTSIDE the branches that draw the screen: a read that fails draws the problem in the
 * list's place, and a component inside that branch is made again when the list comes back and
 * forgets that it saw no folder, so it would never offer. Read from the source: a unit environment
 * runs no library.
 */
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
