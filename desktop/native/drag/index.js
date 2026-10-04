'use strict'

/* The native file drag-out addon.
 *
 * Electron's own `startDrag` advertises the drop effect `copyLink`, which some chat applications'
 * drop handlers refuse, and the effect is not configurable (electron#15361, closed as not
 * planned). So the OS drag is driven directly: on Windows via OLE's DoDragDrop, advertising
 * `move`.
 *
 * Windows only: code for a platform nobody builds or tests is a cost that buys nothing.
 *
 * This is ABI-bound to one Electron version and is rebuilt by the release script whenever
 * Electron moves. An Electron upgrade is a release event, not a dependency bump.
 */

if (process.platform !== 'win32') {
	throw new Error('native drag: Windows only')
}

const addon = require('./build/Release/drag.node')

module.exports = {
	/** Begin an OS drag of one real file. Synchronous: it enters OLE's modal loop until the drop. */
	startDrag(filePath) {
		return addon.startDrag(filePath)
	},

	/**
	 * Begin an OS drag of a file that is STILL ARRIVING.
	 *
	 * For client mode, where the bytes are on another machine and a download cannot happen inside a
	 * drag: the receiver is handed a name and a size now, and reads the file after the drop while it
	 * is still being written. `partial` is the file being written, `finished` is where it is renamed
	 * when it completes, and `total` is -1 when the size is not known.
	 *
	 * Synchronous like the other, but the object it hands over OUTLIVES this call: a receiver doing
	 * its extraction on a thread of its own goes on reading after the drop.
	 */
	startStreamedDrag(partial, finished, name, total) {
		return addon.startStreamedDrag(partial, finished, name, total)
	}
}
