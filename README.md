# Phone Deck

Phone Deck is a beta Android companion app and Windows control deck. Connect over Wi-Fi or USB using Android Debug Bridge (ADB reverse).

## Features

- Trigger keyboard shortcuts and type text on the PC.
- Control volume and media playback.
- Open apps and websites from the phone.
- Customize button labels, icons, layouts, themes, and backgrounds.
- Switch between active Windows apps.
- Run the Windows app in the system tray.

## Windows app

The repository includes the ready-to-run `PhoneDeck.exe` and the Python source used to build it.

- Double-click `PhoneDeck.exe` to launch the app.
- To run from source, install Python and the dependencies listed in `build_exe.bat`, then double-click `Baslat.bat`.
- To rebuild the executable, run `build_exe.bat`.

## Project files

- `server.py` — Windows server and control interface
- `PhoneDeck.exe` — packaged Windows application
- `Baslat.bat` — starts the Python version without a console window
- `build_exe.bat` and `PhoneDeck.spec` — PyInstaller build setup
- `TelefonDeck.ico` — application icon
- `MainActivity.kt` — Android connection app source (incomplete project)

## License
- MIT licence
