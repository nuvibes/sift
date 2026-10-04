/* The names of the channels the page and the main process talk over.
 *
 * Strings only, because the preload imports them and has no main-process APIs: pulling in
 * `ipcMain` would make the preload throw silently and the application open without its bridge.
 */

/** Which of the channels below the page asking may use. The preload asks it once, as it loads. */
export const BRIDGE_VERBS = 'sift:bridgeVerbs';

/** Ask the operating system for a folder. The whole of the decision's grant mechanism. */
export const CHOOSE_FOLDER = 'sift:chooseFolder';

/**
 * Ask the operating system for one database file (Migrate from Stash). The page learns only the
 * path somebody pointed at, which the server confines to the folders Sift has been given.
 */
export const CHOOSE_FILE = 'sift:chooseFile';

/** Drag one asset's file out of the window into another application. */
export const START_DRAG = 'sift:startDrag';

/** Read what is on the clipboard, for the Add panel's Paste button. */
export const READ_CLIPBOARD = 'sift:readClipboard';

/* A picture of this window, or one area of it, saved by the page through the download door. */
export const CAPTURE_WINDOW = 'sift:captureWindow';

/** Save the address of the computer running the library, and go there. Client mode's first run. */
export const SAVE_SERVER = 'sift:saveServer';

/** The address last tried, so a correction starts from it rather than from blank. */
export const LAST_SERVER = 'sift:lastServer';

/** Where the connect screen stands: the last address, why it did not answer, and the saved ones. */
export const CONNECT_STATE = 'sift:connectState';

/** Take one saved address off the list. What comes back is the list afterwards. */
export const FORGET_SERVER = 'sift:forgetServer';

/** Download the newest release, prove it is Sift's, and launch it. Takes NOTHING (see update.ts). */
export const APPLY_UPDATE = 'sift:applyUpdate';

/** How a client-mode fetch reports itself back to the page while it runs. Main -> page only. */
export const DRAG_PROGRESS = 'sift:dragProgress';

/** Whether this library is offered to the rest of the network, and the address to reach it at. */
export const GET_SHARING = 'sift:getSharing';

/** Turn that on or off. Takes effect when Sift is next opened: the address is bound at start. */
export const SET_SHARING = 'sift:setSharing';

/** What version THIS copy of the application is, which is not always the library's. */
export const SHELL_VERSION = 'sift:shellVersion';
/* The end of the SHELL's own log: in client mode the server is another computer with its own. */
export const SHELL_LOG = 'sift:shellLog';

/* Whether the shell's log writes detail too (`Settings > Activity > Log`). A true or false in. */
export const SHELL_LOG_DETAIL = 'sift:shellLogDetail';

/** Whether Windows lets other computers reach Sift on its port. A plain read; no rights needed. */
export const GET_FIREWALL = 'sift:getFirewall';

/** Ask Windows to open that port. Raises the operating system's own elevation prompt. */
export const OPEN_FIREWALL = 'sift:openFirewall';

/** What the computer the window is ON is, for a client whose library lives elsewhere. */
export const LOCAL_HARDWARE = 'sift:localHardware';

/* What this computer is CALLED, for the phone's remote: every mode, unlike `LOCAL_HARDWARE`. */
export const MACHINE_NAME = 'sift:machineName';

/** Where a file saved out of Sift lands, and whether that is this machine's own Downloads folder. */
export const GET_DOWNLOAD_DIR = 'sift:getDownloadDir';

/** Choose a different one, or pass null to go back to the machine's own. */
export const SET_DOWNLOAD_DIR = 'sift:setDownloadDir';

/** Show a backup Sift saved on this computer in its folder, in the system's own file manager. */
export const SHOW_IN_FOLDER = 'sift:showInFolder';

/** Which browsers this machine has, so a person can choose where links open. */
export const LIST_BROWSERS = 'sift:listBrowsers';

/** Choose one, or `null` for whatever Windows would have used. */
export const SET_BROWSER = 'sift:setBrowser';

/** Paint the window's own caption buttons in the colours the page is drawing itself in. */
export const SET_TITLE_BAR = 'sift:setTitleBar';

/* Where Sift keeps its own two folders, and moving them; read apart from write, as every pair. */
export const GET_STORAGE = 'sift:getStorage';
export const MOVE_STORAGE = 'sift:moveStorage';
/** How far a move has got, pushed while it runs. See DRAG_PROGRESS, which is the same shape. */
export const STORAGE_PROGRESS = 'sift:storageProgress';

/* The two questions before a backend exists, on Sift's own screens served by `shellpage.ts`. The
 * folder dialog stays the operating system's, so the folder is one somebody pointed at. */

/** Which lifecycle this copy runs: its own library, or a window onto somebody else's. */
export const CHOOSE_MODE = 'sift:chooseMode';

/** Where Sift would put the library if nobody said otherwise, for the screen that offers it. */
export const SUGGESTED_LIBRARY = 'sift:suggestedLibrary';

/** Keep the library in the suggested folder, or in one chosen from the machine's own dialog. */
export const CHOOSE_LIBRARY = 'sift:chooseLibrary';

/** Where taking that folder has got, pushed while it runs, so the screen says each step in turn. */
export const SETUP_PROGRESS = 'sift:setupProgress';

/* Go back a question: `FORGET_MODE`'s write, plus a redraw so the earlier question shows now. */
export const SETUP_BACK = 'sift:setupBack';

/* Whether closing the window leaves Sift running in the notification area: about this machine,
 * so the shell holds it. */
export const GET_KEEP_RUNNING = 'sift:getKeepRunning';
export const SET_KEEP_RUNNING = 'sift:setKeepRunning';

/* Whether Sift starts at Windows sign-in; answers what Windows says afterwards (startup.ts). */
export const GET_START_WITH_WINDOWS = 'sift:startsWithWindows';
export const SET_START_WITH_WINDOWS = 'sift:startWithWindows';

/* The libraries this copy has opened. `OPEN_LIBRARY` names one already on the list, never a path;
 * `ADD_LIBRARY` grants a path only through the operating system's picker (`planForDatabase`). */
export const LIST_LIBRARIES = 'sift:libraries';
export const OPEN_LIBRARY = 'sift:openLibrary';
export const ADD_LIBRARY = 'sift:addLibrary';
export const FORGET_LIBRARY = 'sift:forgetLibrary';

/* Forget which way Sift was set up. The library folder stays, so switching back finds it. */
export const FORGET_MODE = 'sift:forgetMode';

/* Close Sift and open it again, nothing more. Not offered to another computer (`REMOTE_VERBS`). */
export const RESTART_APP = 'sift:restartApp';
