# Windows installer

Builds a per-user installer that bundles Python 3.11, all dependencies and the
built web UI. The result is one file:

```
installer/dist/PhotoFinder-0.2.0-setup.exe
```

## What it installs

- `%LOCALAPPDATA%\Programs\PhotoFinder` — application, bundled Python, web UI
- Start Menu shortcuts: **Photo Finder**, **Photo Finder data folder**,
  **Stop Photo Finder**, **Uninstall Photo Finder**
- Optional desktop shortcut (unchecked by default)
- No admin rights, no services, no autostart

Your library lives in `%LOCALAPPDATA%\PhotoFinder\data` (SQLite database, FAISS
index, thumbnails). Upgrades and uninstalling leave that folder alone, so the
index survives. `server.log` in that folder is the place to look if the app does
not start.

## Build

Requirements: Windows 10/11 x64, [uv](https://docs.astral.sh/uv/), Node 20+,
[Inno Setup 6](https://jrsoftware.org/isinfo.php) and the .NET Framework 4.x
compiler that ships with Windows.

```powershell
# from the repository root
.\installer\build.ps1
```

The script builds the React app, copies a managed Python 3.11 runtime, exports
the exact locked dependencies with `uv export`, installs them into the payload,
draws the icon, compiles the windowless launcher and runs Inno Setup.
Install size is about 830 MB; the compressed setup is about 130 MB.

Useful switches:

```powershell
.\installer\build.ps1 -SkipWeb        # reuse web\dist
.\installer\build.ps1 -SkipDeps       # reuse the payload's installed packages
.\installer\build.ps1 -SkipCompile    # reuse PhotoFinder.exe
.\installer\build.ps1 -Clean          # wipe installer\build first
```

`-SkipDeps` is only safe after a complete build; it also skips copying the
Python runtime.

## Layout of the installed app

```
PhotoFinder.exe        windowless launcher (icon, no console)
python\                bundled CPython 3.11 + site-packages
app\run.py             points PHOTOFINDER_DATA at %LOCALAPPDATA%\PhotoFinder\data
app\photofinder\       application package
web\dist\              built UI served by FastAPI
```

`PhotoFinder.exe` with no arguments starts the desktop shell (`run.py desktop`):
the API runs hidden, the browser opens, and a tray icon offers **Open**,
**Open data folder** and **Quit**. `PhotoFinder.exe stop` stops the server from
a shortcut or terminal. Pass normal CLI arguments through the launcher, for
example `PhotoFinder.exe index "D:\Photos"`.

## Test an install silently

```powershell
$setup = ".\installer\dist\PhotoFinder-0.2.0-setup.exe"
Start-Process $setup -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART" -Wait
& "$env:LOCALAPPDATA\Programs\PhotoFinder\python\python.exe" `
  "$env:LOCALAPPDATA\Programs\PhotoFinder\app\run.py" stats
```

The installer stops a running Photo Finder before replacing files. The
uninstaller stops it too and keeps `%LOCALAPPDATA%\PhotoFinder\data`.

## First run

The first search loads CLIP and downloads roughly 350 MB of weights from
Hugging Face once (cached for later runs). Everything after that is local.

`ffmpeg` and `ffprobe` are not bundled; they are only needed to index videos.
Put them on `PATH` or set `PHOTOFINDER_FFMPEG` / `PHOTOFINDER_FFPROBE`.

## Licence notes

The installer bundles Python (PSF licence), PyTorch, FAISS, open_clip, FastAPI,
React and the other dependencies listed in `server/pyproject.toml`. It does not
bundle CLIP weights or ffmpeg. Check the model and third-party terms before
commercial distribution; the root README has the details.
