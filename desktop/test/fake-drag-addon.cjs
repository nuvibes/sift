/* The compiled drag addon, as a test can hold it.
 *
 * The real one is a Windows binary bound to one Electron ABI: it will not load in the plain Node the
 * test runner is, on any platform. What a test needs is not the OLE drag loop (that needs a hand
 * on a mouse) but the ARGUMENTS it was entered with, because those are what decide whether the
 * receiving application gets the right file, under the right name, at the right size.
 *
 * A `.cjs` file rather than a module the test defines, because `verbs.ts` reaches the addon through
 * `require(path)` on purpose: a compiled binary must not be able to stop the main process loading.
 * A path is what it wants, so a path is what the test gives it.
 */

const calls = [];

module.exports = {
	calls,
	startDrag(path) {
		calls.push({ verb: 'startDrag', path });
	},
	startStreamedDrag(partial, finished, name, total) {
		calls.push({ verb: 'startStreamedDrag', partial, finished, name, total });
	}
};
