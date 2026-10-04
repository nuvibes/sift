@echo off
rem SPDX-License-Identifier: AGPL-3.0-or-later
rem
rem Builds the pillow-heif wheel for Windows x64 WITHOUT the x265 encoder.
rem
rem Sift reads HEIF pictures and never writes one, so the HEVC encoder the published wheel links
rem (libx265, 22.6 MB, GPL-2.0-or-later) is never called. This recipe builds the same three pieces
rem at the same versions with the encoder left out:
rem
rem   libde265 1.1.3   the HEVC decoder, LGPL-3.0
rem   libheif 1.23.4   the HEIF container, LGPL-3.0, configured with WITH_X265=OFF
rem   pillow-heif 1.8.0  the Python binding, BSD-3-Clause, built by its own setup.py
rem
rem The upstream wheel builds libheif with MSYS2's MinGW compilers (the PKGBUILD under
rem libheif/windows in pillow-heif's source distribution). This builds everything with MSVC, which
rem also drops the three MinGW runtime DLLs the upstream wheel carries. The CMake options below are
rem that PKGBUILD's, with the x265 line turned off.
rem
rem The two library archives are read from vendor\bin\sources, where scripts\fetch_vendor.py puts
rem them after checking their SHA-256, so the wheel is built from exactly the source that ships
rem beside it. The pillow-heif source distribution is the one uv.lock pins, checked here by the same
rem SHA-256.
rem
rem Needs: Visual Studio 2022 Build Tools with the C++ workload (MSVC, CMake and Ninja come with
rem it), uv on PATH, and network access for the build requirements. About a minute.
rem
rem Usage:  scripts\build_pillow_heif.bat
rem Result: vendor\wheels\pillow_heif-1.8.0-cp313-cp313-win_amd64.whl
setlocal EnableExtensions

set "ROOT=%~dp0.."
if "%SOURCES%"=="" set "SOURCES=%ROOT%\vendor\bin\sources"
if "%WORK%"=="" set "WORK=%ROOT%\build\pillow-heif"
if "%OUT%"=="" set "OUT=%ROOT%\vendor\wheels"
if "%PYTHON%"=="" set "PYTHON=3.13"
set "SDIST_URL=https://files.pythonhosted.org/packages/bb/4c/d5319a1f276c70528ff97893afc42a300ff28029e27ca8de89bb3b271680/pillow_heif-1.8.0.tar.gz"
set "SDIST_SHA256=e47c27432c6fd3d66c22f0de9f27fd379383b646c947520bc485854ce72060d0"

rem Windows' own tar, by its path: a tar from a Unix-style shell earlier on PATH reads the drive
rem letter of an archive's path as a host name and extracts nothing.
set "TAR=%SystemRoot%\System32\tar.exe"

rem The same compiler and inputs make the same wheel byte for byte, so a rebuild on the release
rem machine is held to the pinned digest: /Brepro has the compiler and the linker write a digest of
rem the content where they would write the time, SOURCE_DATE_EPOCH gives every member of the wheel
rem one date and keeps the machine's paths out of its DELVEWHEEL record, and the build requirements
rem are resolved as they stood on one day. Another MSVC release still makes another file, which is
rem why CI holds its own build to the libraries it carries rather than to the digest.
set "SOURCE_DATE_EPOCH=315532800"
set "RESOLVED_AS_OF=2026-10-01T00:00:00Z"

set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
for /f "usebackq delims=" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VS=%%i"
if not defined VS (
  echo No Visual Studio with the C++ tools was found. Install the Build Tools' C++ workload.
  exit /b 1
)
call "%VS%\VC\Auxiliary\Build\vcvars64.bat" >nul || (
  echo The C++ build environment did not load from "%VS%".
  exit /b 1
)
set "_CL_=/Brepro"
set "_LINK_=/Brepro"

if exist "%WORK%" rmdir /s /q "%WORK%"
mkdir "%WORK%" || exit /b 1
set "PREFIX=%WORK%\prefix"

for %%a in (libde265-1.1.3.tar.gz libheif-1.23.4.tar.gz) do (
  if not exist "%SOURCES%\%%a" (
    echo %SOURCES%\%%a is missing. Run scripts\fetch_vendor.py first.
    exit /b 1
  )
  "%TAR%" -xzf "%SOURCES%\%%a" -C "%WORK%" || exit /b 1
)

curl.exe -sSL -o "%WORK%\pillow_heif-1.8.0.tar.gz" "%SDIST_URL%" || exit /b 1
certutil -hashfile "%WORK%\pillow_heif-1.8.0.tar.gz" SHA256 | findstr /i /x "%SDIST_SHA256%" >nul
if errorlevel 1 (
  echo pillow_heif-1.8.0.tar.gz does not have the SHA-256 uv.lock pins. Nothing was built.
  exit /b 1
)
"%TAR%" -xzf "%WORK%\pillow_heif-1.8.0.tar.gz" -C "%WORK%" || exit /b 1

rem libde265: the library only. Its sample decoder and encoder are programs Sift never runs.
cmake -G Ninja -S "%WORK%\libde265-1.1.3" -B "%WORK%\build-libde265" ^
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON "-DCMAKE_INSTALL_PREFIX=%PREFIX%" ^
  -DENABLE_SDL=OFF -DENABLE_DECODER=OFF -DENABLE_ENCODER=OFF || exit /b 1
cmake --build "%WORK%\build-libde265" || exit /b 1
cmake --install "%WORK%\build-libde265" || exit /b 1

rem libheif: the PKGBUILD's options, with WITH_X265 OFF. That is the whole point of this file.
cmake -G Ninja -S "%WORK%\libheif-1.23.4" -B "%WORK%\build-libheif" ^
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON "-DCMAKE_INSTALL_PREFIX=%PREFIX%" ^
  "-DCMAKE_PREFIX_PATH=%PREFIX%" ^
  -DWITH_LIBDE265=ON -DWITH_LIBDE265_PLUGIN=OFF ^
  -DWITH_X265=OFF -DWITH_X265_PLUGIN=OFF ^
  -DWITH_OPENJPH_DECODER=OFF -DWITH_OPENJPH_ENCODER=OFF -DWITH_HEADER_COMPRESSION=OFF ^
  -DWITH_AOM_DECODER=OFF -DWITH_AOM_DECODER_PLUGIN=OFF -DWITH_AOM_ENCODER=OFF -DWITH_AOM_ENCODER_PLUGIN=OFF ^
  -DWITH_RAV1E=OFF -DWITH_RAV1E_PLUGIN=OFF -DWITH_DAV1D=OFF -DWITH_DAV1D_PLUGIN=OFF ^
  -DWITH_SvtEnc=OFF -DWITH_SvtEnc_PLUGIN=OFF -DWITH_KVAZAAR=OFF -DWITH_KVAZAAR_PLUGIN=OFF ^
  -DWITH_FFMPEG_DECODER=OFF -DWITH_FFMPEG_DECODER_PLUGIN=OFF ^
  -DWITH_JPEG_DECODER=OFF -DWITH_JPEG_ENCODER=OFF -DWITH_OpenJPEG_DECODER=OFF -DWITH_OpenJPEG_ENCODER=OFF ^
  -DENABLE_PLUGIN_LOADING=OFF -DWITH_LIBSHARPYUV=OFF -DWITH_GDK_PIXBUF=OFF ^
  -DWITH_EXAMPLES=OFF -DBUILD_TESTING=OFF || exit /b 1
cmake --build "%WORK%\build-libheif" || exit /b 1
cmake --install "%WORK%\build-libheif" || exit /b 1

rem pillow-heif's setup.py looks for an MSYS2 layout and links `libheif.lib`; MSVC names it heif.lib.
if not exist "%PREFIX%\lib\libheif.lib" copy /y "%PREFIX%\lib\heif.lib" "%PREFIX%\lib\libheif.lib" >nul || exit /b 1
set "MSYS2_PREFIX=%PREFIX%"
uv build --wheel --python %PYTHON% --exclude-newer %RESOLVED_AS_OF% --out-dir "%WORK%\built" "%WORK%\pillow_heif-1.8.0" || exit /b 1

rem The two libraries go into the wheel beside the extension, the way the published wheel carries
rem them (delvewheel, the version upstream uses).
if not exist "%OUT%" mkdir "%OUT%"
for %%w in ("%WORK%\built\pillow_heif-*.whl") do (
  uvx --exclude-newer %RESOLVED_AS_OF% --from delvewheel==1.13.1 delvewheel repair -w "%OUT%" "%%w" --add-path "%PREFIX%\bin" || exit /b 1
)
echo.
echo Built into %OUT%. Pin its SHA-256 under "wheels" in scripts\vendor_manifest.json,
echo then check it with: uv run python scripts\fetch_vendor.py --verify-only
endlocal
