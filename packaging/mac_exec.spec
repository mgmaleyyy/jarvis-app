# PyInstaller spec — macOS .app pro J.A.R.V.I.S. Executive
a = Analysis(["../exec/jarvis.py"], datas=[("../exec/ui", "ui")], hiddenimports=[], excludes=["tkinter"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="JARVIS-Executive", console=False, argv_emulation=False)
coll = COLLECT(exe, a.binaries, a.datas, name="JARVIS-Executive")
app = BUNDLE(coll, name="J.A.R.V.I.S. Executive.app", icon="../build/AppIcon.icns", bundle_identifier="cz.jarvis.executive",
             info_plist={"CFBundleDisplayName": "J.A.R.V.I.S. Executive", "CFBundleShortVersionString": "1.0.0", "CFBundleVersion": "1.0.0",
                         "LSMinimumSystemVersion": "11.0", "NSHighResolutionCapable": True,
                         "NSAppleEventsUsageDescription": "Ovládání hudby (Spotify/Music) a hlasitosti.",
                         "NSMicrophoneUsageDescription": "Hlasové příkazy."})
