// The client renders in the browser and nowhere else.
//
// There is no server to render on: the pages are built once and served as files by the Python
// application. Turning server rendering off is what makes that honest: every page gets its data
// from /api with the user's session, after the app has started, which is the only way the server's
// answer can be the one that counts.
export const ssr = false;

// Nothing is prerendered either. Every page needs a session before it has anything to show, so a
// build-time render could only ever produce the empty one.
export const prerender = false;
