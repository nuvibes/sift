; What the installer and the uninstaller do and say, beyond what electron-builder writes.
;
; 1. Sift indexes media where it is, so the uninstaller says plainly that the photos and videos are
;    never touched and that the data Sift built is kept unless you ask otherwise. That is the page
;    below.
;
; 2. electron-builder copies the whole installer into %LOCALAPPDATA% on every install for
;    electron-updater, which Sift does not use (its updater reads the release feed, checks a
;    signature and launches the installer itself). The copy is removed at the end of the install and
;    again on uninstall.
;
; 3. The install side's pages speak the same way the uninstall page does. electron-builder's pages
;    are kept (they are what the updater drives) and only their words are Sift's: the folder page,
;    the progress page and the finish page, each set below through the settings Modern UI reads
;    as it builds that page.
;
; 4. There is no install-mode page. Sift installs per user (`perMachine: false` in
;    electron-builder.yml) and never asks for administrator rights to install; the stock page
;    asked "anyone who uses this computer, or only me", and the first answer raised Windows'
;    administrator prompt for an installer that is per user by design, and a per-machine copy the
;    updater, which runs as the person, could not update. `customInstallMode` settles it.
;
; Nothing is deleted without being asked for, and the box is off by default.

!include FileFunc.nsh
!include LogicLib.nsh
!include nsDialogs.nsh

; Where this installation actually keeps its library. NOT a constant: the folder is chosen at first
; run, so `%LOCALAPPDATA%\Sift` is only the default. The application records the real one in the
; registry on every start (see desktop/src/uninstall.ts, which is the other half of this).
!define SIFT_KEY "Software\Sift"

; What the page lists and the uninstall deletes when the box is ticked: every library the libraries
; folder holds, told from anything else in it by the database a library keeps in its data folder.
; The same file name the application checks (`DATABASE_FILENAME` in desktop/src).
!define SIFT_DATABASE "data\sift.sqlite3"

; All six are the uninstaller's, and they are declared only in its pass for the same reason the
; function below is defined only there: NSIS refuses to compile a script holding a variable nothing
; references, and electron-builder builds the installer from this same file.
!ifdef BUILD_UNINSTALLER
Var siftDataDir
Var siftCacheDir
Var siftBesideDir
Var siftLibrariesDir
Var siftRemoveEverything
Var siftRemoveBox
!endif

; --- The words on electron-builder's pages ------------------------------------------------------
;
; Each is a setting Modern UI reads while it builds one page and forgets after it, so they are set
; here, before any page is built, and each lands on the one page it names. The two passes build
; different pages from this same file, so each pass sets only its own.
!ifndef BUILD_UNINSTALLER
  ; The folder page. It is the program's folder only, and it says so: the library is chosen when
  ; Sift first opens, which is where somebody would otherwise look for it.
  !define MUI_PAGE_HEADER_TEXT "Where to install Sift"
  !define MUI_PAGE_HEADER_SUBTEXT "Sift installs for you only, so Windows doesn't ask for administrator rights."
  !define MUI_DIRECTORYPAGE_TEXT_TOP "Sift's program goes in this folder. Your library is kept somewhere else, and Sift asks where the first time it opens."
  !define MUI_DIRECTORYPAGE_TEXT_DESTINATION "Folder"
  ; The progress page once it has finished, or stopped.
  !define MUI_INSTFILESPAGE_FINISHHEADER_TEXT "Sift is installed"
  !define MUI_INSTFILESPAGE_FINISHHEADER_SUBTEXT "Press Next to finish."
  !define MUI_INSTFILESPAGE_ABORTHEADER_TEXT "Sift wasn't installed"
  !define MUI_INSTFILESPAGE_ABORTHEADER_SUBTEXT "The installation stopped before it finished. Run the installer again to try again."
  ; The finish page. Its tick box opens Sift (electron-builder's own `StartApp`).
  !define MUI_FINISHPAGE_TITLE "Sift is installed"
  !define MUI_FINISHPAGE_TEXT "The first time Sift opens, it asks where to keep your library and which folders to look in.$\r$\n$\r$\nYour photos and videos stay where they are."
  !define MUI_FINISHPAGE_RUN_TEXT "Open Sift now"
!else
  ; The progress page and the finish page after the page below.
  !define MUI_PAGE_HEADER_TEXT "Uninstalling Sift"
  !define MUI_PAGE_HEADER_SUBTEXT "This takes a moment."
  !define MUI_INSTFILESPAGE_FINISHHEADER_TEXT "Sift is uninstalled"
  !define MUI_INSTFILESPAGE_FINISHHEADER_SUBTEXT "Press Next to finish."
  !define MUI_FINISHPAGE_TITLE "Sift is uninstalled"
  !define MUI_FINISHPAGE_TEXT "Your photos and videos are where they were."
!endif

; The progress page's header while it runs. Set after the folder page has taken its own, through
; the one place electron-builder offers between the two.
!macro customPageAfterChangeDir
  !ifndef MUI_PAGE_HEADER_TEXT
    !define MUI_PAGE_HEADER_TEXT "Installing Sift"
    !define MUI_PAGE_HEADER_SUBTEXT "This takes a minute."
  !endif
!macroend

; Per user, always, and the install-mode page is never shown (see 4 above). Only in the installer's
; pass: the uninstaller must still find a copy an older installer put in for everybody.
!macro customInstallMode
  !ifndef BUILD_UNINSTALLER
    StrCpy $isForceCurrentInstall "1"
  !endif
!macroend

; --- Install ----------------------------------------------------------------------------------

!macro customInstall
  ; Runs after the application files are in place, which is after the copy above was made.
  ; SetShellVarContext current, because the copy is written to the CURRENT user's LOCALAPPDATA even
  ; on a per-machine install: electron always uses per-user application data.
  !ifdef APP_INSTALLER_STORE_FILE
    SetShellVarContext current
    Delete "$LOCALAPPDATA\${APP_INSTALLER_STORE_FILE}"
    ; The folder's name is electron-builder's to choose, so it is READ OFF the path it gave us
    ; rather than written down a second time here. RMDir without /r, so a folder that somehow holds
    ; something else is left alone rather than taken with it.
    ${GetParent} "$LOCALAPPDATA\${APP_INSTALLER_STORE_FILE}" $0
    RMDir "$0"
  !endif
!macroend

; --- Uninstall --------------------------------------------------------------------------------

; Under the box: what ticking it also deletes, with every other library named in a list, and what
; leaving it means. BEFORE and AFTER are the sentences the two cases say either side of that.
;
; The list is the same walk the uninstall runs (`un.siftEachLibrary`), so what is listed is what
; goes, and a library kept in a folder of its own elsewhere is never on it, which the words say.
!macro siftNameTheLibraries BEFORE AFTER
  StrCpy $R9 ""
  Call un.siftEachLibrary
  ${If} $R8 > 0
    ${NSD_CreateLabel} 14u 62u 95% 26u "${BEFORE} Deleting it also deletes these other libraries, and any files Sift quarantined. Libraries you keep in other folders stay."
    Pop $0
    ${NSD_CreateListBox} 14u 89u 95% 30u ""
    Pop $R9
    Call un.siftEachLibrary
    ${NSD_CreateLabel} 14u 121u 95% 20u "${AFTER}"
    Pop $0
  ${Else}
    ${NSD_CreateLabel} 14u 62u 95% 40u "${BEFORE} Deleting it also deletes any files Sift quarantined. ${AFTER}"
    Pop $0
  ${EndIf}
!macroend

; The page and its two functions live inside this macro because electron-builder includes this file
; before MUI2 and expands the macro after it: `MUI_HEADER_TEXT` only exists at expansion time.
;
; The macro is inserted exactly once, which is what makes defining functions inside it legal.
!macro customUnWelcomePage
  UninstPage custom un.siftFarewell un.siftFarewellLeave

Function un.siftFarewell
  ReadRegStr $siftDataDir HKCU "${SIFT_KEY}" "LibraryData"
  ReadRegStr $siftCacheDir HKCU "${SIFT_KEY}" "CacheData"
  StrCpy $siftBesideDir ""

  ; The reassurance is the header's one line and is not said again below it: said twice, it reads
  ; as protesting.
  !insertmacro MUI_HEADER_TEXT "Uninstall Sift" "Your photos and videos stay exactly where they are."

  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}

  ; Nothing recorded is not the same as nothing there. The record is cleared on every launch in
  ; client mode, so a machine that ran a library before switching can have a database in the
  ; default place with nothing pointing at it. It is looked for.
  ;
  ; Only the default place: a folder chosen at first run cannot be guessed, and guessing is how an
  ; uninstaller deletes the wrong directory.
  ${If} $siftDataDir == ""
  ${AndIf} ${FileExists} "$LOCALAPPDATA\Sift\*.*"
    ; The whole default folder is taken here, so it already holds the cache, the models and the
    ; libraries folder, and nothing beside it is Sift's. The libraries are walked only to be named.
    StrCpy $siftDataDir "$LOCALAPPDATA\Sift"
    StrCpy $siftCacheDir ""
    StrCpy $siftLibrariesDir "$LOCALAPPDATA\Sift\libraries"
    ${NSD_CreateLabel} 0 0 100% 44u "Sift was set up on this device to open a library on another device, so there is no library here. There is still some Sift data from before, in:$\r$\n$\r$\n      $siftDataDir"
    Pop $0
    ${NSD_CreateCheckbox} 0 46u 100% 14u "Also delete Sift's data (the database and settings)"
    Pop $siftRemoveBox
    !insertmacro siftNameTheLibraries "Nothing on this device uses it." "If you leave it, you can install Sift again later, choose This device and pick the same folder. Sift picks up where it left off."
  ${ElseIf} $siftDataDir == ""
    ; Nothing recorded and nothing in the default place: a client installation that never held a
    ; library, or one that was never opened. Offering anyway would name a folder that is not there.
    ${NSD_CreateLabel} 0 0 100% 40u "Sift didn't keep any data of its own on this computer, so there is nothing else to remove."
    Pop $0
  ${Else}
    ; The libraries folder as the application found it (see desktop/src/uninstall.ts): from a library
    ; inside that folder it is two levels up, and "beside the data folder" would be that library's own
    ; folder, and its siblings would be missed. A record without the value falls back to beside the
    ; data folder, which is right for the first library.
    ReadRegStr $siftLibrariesDir HKCU "${SIFT_KEY}" "LibrariesFolder"
    ${If} $siftLibrariesDir == ""
      ${GetParent} $siftDataDir $0
      StrCpy $siftLibrariesDir "$0\libraries"
    ${EndIf}
    ; The models and the first library sit in the folder above the libraries folder. That folder is
    ; Sift's own unless it is a drive's root, where the models live inside the data folder and a
    ; "libraries" folder may be somebody's own, so nothing beside it is touched there.
    ${GetParent} $siftLibrariesDir $siftBesideDir
    StrLen $1 $siftBesideDir
    ${If} $1 <= 3
      StrCpy $siftBesideDir ""
      StrCpy $siftLibrariesDir ""
    ${EndIf}
    ${NSD_CreateLabel} 0 0 100% 44u "Sift keeps its own data on this device: the database with your tags, people and ratings, its settings, its thumbnails and the models it downloaded. It's in:$\r$\n$\r$\n      $siftDataDir"
    Pop $0
    ${NSD_CreateCheckbox} 0 46u 100% 14u "Also delete Sift's data (the database and settings)"
    Pop $siftRemoveBox
    ; What a later install does with a kept folder is said in the words the first run uses: only
    ; "This device" opens a library here, and "Another device" never reads this folder at all.
    !insertmacro siftNameTheLibraries "" "If you leave it, Sift offers to carry on with it the next time you install it and choose This device."
  ${EndIf}

  nsDialogs::Show
FunctionEnd

Function un.siftFarewellLeave
  ${If} $siftRemoveBox != ""
    ${NSD_GetState} $siftRemoveBox $siftRemoveEverything
  ${EndIf}
FunctionEnd
!macroend

; Remove one recorded folder, having first refused the ones that would be a catastrophe.
;
; The path comes from the registry, and a truncated or hand-edited value can be a drive root, where
; `RMDir /r` does not ask twice. So: nothing shorter than a drive plus a name, and never something
; ending in `:\`.
; Only in the uninstaller's own build. electron-builder compiles this script twice, the second time
; with BUILD_UNINSTALLER defined, and a top-level `un.` function in the installer pass fails the
; compile ("Uninstaller script code found but WriteUninstaller never used"). The page above avoids
; this because its macro is inserted only in the uninstaller pass.
!ifdef BUILD_UNINSTALLER
Function un.siftRemoveFolder
  Exch $R0
  Push $R1
  Push $R2

  StrLen $R1 $R0
  StrCpy $R2 $R0 2 -2
  ${If} $R1 > 4
  ${AndIf} $R2 != ":\"
    RMDir /r "$R0"
  ${EndIf}

  Pop $R2
  Pop $R1
  Pop $R0
FunctionEnd

; Every library the libraries folder holds, and the first library above it, other than the one this
; installation recorded (the page names that one at the top, and the uninstall takes it on its own).
;
; ONE WALK FOR THE LIST AND FOR THE DELETE, so they cannot disagree: $R9 holds what to do with each
; library found ("" counts, "remove" deletes, anything else is the list box to add it to), and $R8
; is how many there were. A folder counts only where it holds a library's database: the libraries
; folder is Sift's, but a folder somebody dropped in it is theirs.
Function un.siftEachLibrary
  Push $R0
  Push $R1
  StrCpy $R8 0

  ${If} $siftBesideDir != ""
  ${AndIf} ${FileExists} "$siftBesideDir\${SIFT_DATABASE}"
  ${AndIf} "$siftBesideDir\data" != $siftDataDir
    IntOp $R8 $R8 + 1
    ${If} $R9 == "remove"
      Push "$siftBesideDir\data"
      Call un.siftRemoveFolder
      Push "$siftBesideDir\cache"
      Call un.siftRemoveFolder
    ${ElseIf} $R9 != ""
      ${NSD_LB_AddString} $R9 "$siftBesideDir"
    ${EndIf}
  ${EndIf}

  ${If} $siftLibrariesDir != ""
    FindFirst $R0 $R1 "$siftLibrariesDir\*"
    ${DoWhile} $R1 != ""
      ${If} $R1 != "."
      ${AndIf} $R1 != ".."
      ${AndIf} ${FileExists} "$siftLibrariesDir\$R1\${SIFT_DATABASE}"
      ${AndIf} "$siftLibrariesDir\$R1\data" != $siftDataDir
        IntOp $R8 $R8 + 1
        ${If} $R9 == "remove"
          Push "$siftLibrariesDir\$R1"
          Call un.siftRemoveFolder
        ${ElseIf} $R9 != ""
          ${NSD_LB_AddString} $R9 "$siftLibrariesDir\$R1"
        ${EndIf}
      ${EndIf}
      FindNext $R0 $R1
    ${Loop}
    FindClose $R0
  ${EndIf}

  Pop $R1
  Pop $R0
FunctionEnd
!endif

!macro customUnInstall
  ; ${isUpdated} is true when an install runs this uninstaller to clear the previous version.
  ; Deleting the library there would turn every update into a wipe; no page is shown in that case,
  ; and the guard keeps the two from ever coming apart.
  ${ifNot} ${isUpdated}
    SetShellVarContext current
    !ifdef APP_INSTALLER_STORE_FILE
      Delete "$LOCALAPPDATA\${APP_INSTALLER_STORE_FILE}"
      ${GetParent} "$LOCALAPPDATA\${APP_INSTALLER_STORE_FILE}" $0
      RMDir "$0"
    !endif

    ${If} $siftRemoveEverything == ${BST_CHECKED}
      Push $siftDataDir
      Call un.siftRemoveFolder
      Push $siftCacheDir
      Call un.siftRemoveFolder
      ; The per-device model store and every other library: what "also delete Sift's data" means
      ; is everything of Sift's own. The libraries are the ones the page listed, by the same walk;
      ; the folder that held them goes only once it is empty, with the mark Sift keeps in it. Only
      ; where the page found a folder above them that is Sift's (see `siftBesideDir`).
      ${If} $siftBesideDir != ""
        Push "$siftBesideDir\models"
        Call un.siftRemoveFolder
        StrCpy $R9 "remove"
        Call un.siftEachLibrary
        Delete "$siftLibrariesDir\sift-libraries.json"
        RMDir "$siftLibrariesDir"
      ${EndIf}
      ; The record goes with the database, and only with it. Left in place, it is what lets the next
      ; installation find the library it had and offer to carry on with it rather than start an
      ; empty one beside it.
      DeleteRegKey HKCU "${SIFT_KEY}"
    ${EndIf}

    ; The shell's own settings go whether the box was ticked or not. They are configuration of the
    ; application (which mode, which server, where links open, whether the library is shared), and a
    ; reinstall on a former client must ask again whether this is the server or a client. An update
    ; never reaches here (see `isUpdated` above).
    RMDir /r "$APPDATA\sift-desktop"
    ; The startup entry "Start Sift when Windows starts" wrote, so Task Manager does not go on
    ; listing a Sift that no longer exists. An update keeps it (see `isUpdated` above).
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Sift"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "Sift"
  ${endIf}
!macroend
