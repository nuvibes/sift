/* The names of the channels the page and the main process talk over. */

/** Which of the channels below the page asking may use. The preload asks it once, as it loads. */
export const BRIDGE_VERBS = 'sift:bridgeVerbs';

/** Ask the operating system for a folder. The whole of the decision's grant mechanism. */
export const CHOOSE_FOLDER = 'sift:chooseFolder';

/** One database file somebody pointed at (Migrate from Stash), confined by the server. */
export const CHOOSE_FILE = 'sift:chooseFile';

export const START_DRAG = 'sift:startDrag';

export const READ_CLIPBOARD = 'sift:readClipboard';

export const CAPTURE_WINDOW = 'sift:captureWindow';

export const SAVE_SERVER = 'sift:saveServer';

export const LAST_SERVER = 'sift:lastServer';

export const CONNECT_STATE = 'sift:connectState';

export const FORGET_SERVER = 'sift:forgetServer';

/** Download the newest release, prove it is Sift's, and launch it. Takes NOTHING (see update.ts). */
export const APPLY_UPDATE = 'sift:applyUpdate';

/** How a client-mode fetch reports itself back to the page while it runs. Main -> page only. */
export const DRAG_PROGRESS = 'sift:dragProgress';

export const GET_SHARING = 'sift:getSharing';

/** Turn that on or off. Takes effect when Sift is next opened: the address is bound at start. */
export const SET_SHARING = 'sift:setSharing';

export const SHELL_VERSION = 'sift:shellVersion';
/* The end of the SHELL's own log: in client mode the server is another computer with its own. */
export const SHELL_LOG = 'sift:shellLog';

export const SAVE_LOG_ARCHIVE = 'sift:saveLogArchive';

export const SHELL_LOG_DETAIL = 'sift:shellLogDetail';

export const GET_FIREWALL = 'sift:getFirewall';

/** Ask Windows to open that port. Raises the operating system's own elevation prompt. */
export const OPEN_FIREWALL = 'sift:openFirewall';

export const LOCAL_HARDWARE = 'sift:localHardware';

export const MACHINE_NAME = 'sift:machineName';

export const GET_DOWNLOAD_DIR = 'sift:getDownloadDir';

export const SET_DOWNLOAD_DIR = 'sift:setDownloadDir';

export const SHOW_IN_FOLDER = 'sift:showInFolder';

export const LIST_BROWSERS = 'sift:listBrowsers';

export const SET_BROWSER = 'sift:setBrowser';

export const SET_TITLE_BAR = 'sift:setTitleBar';

/* Where Sift keeps its own two folders, and moving them; read apart from write, as every pair. */
export const GET_STORAGE = 'sift:getStorage';
export const MOVE_STORAGE = 'sift:moveStorage';
export const STORAGE_PROGRESS = 'sift:storageProgress';

export const CHOOSE_MODE = 'sift:chooseMode';

export const SUGGESTED_LIBRARY = 'sift:suggestedLibrary';

export const CHOOSE_LIBRARY = 'sift:chooseLibrary';

export const SETUP_PROGRESS = 'sift:setupProgress';

export const SETUP_BACK = 'sift:setupBack';

/* Whether closing the window leaves Sift running: about this machine, so the shell holds it. */
export const GET_KEEP_RUNNING = 'sift:getKeepRunning';
export const SET_KEEP_RUNNING = 'sift:setKeepRunning';

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
