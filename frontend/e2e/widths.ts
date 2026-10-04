/*
 * The window sizes the interface is checked at, and the visual comparison photographs it at: the
 * width the design is drawn for, a laptop's, and the Windows snap width (half of a 1920 screen),
 * where the desktop window lands when somebody snaps it to one side. The last is on the list
 * because a fault there (the top bar coming apart, Theater leaving the rail) shows at no other
 * width.
 *
 * One list, so the photographs and the look name the same three sizes. The first is the
 * one the design gallery is photographed at, and it keeps the names its photographs always had.
 */
export const PRESSED_VIEWPORTS = [
	{ width: 1600, height: 1000 },
	{ width: 1280, height: 800 },
	{ width: 960, height: 1000 }
] as const;
