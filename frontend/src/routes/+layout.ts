// The client renders in the browser and nowhere else.
export const ssr = false;

// Nothing is prerendered either. Every page needs a session before it has anything to show, so a
// build-time render could only ever produce the empty one.
export const prerender = false;
