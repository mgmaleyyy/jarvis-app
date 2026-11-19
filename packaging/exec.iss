; Inno Setup — instalacka pro J.A.R.V.I.S. Executive
#define AppName "J.A.R.V.I.S. Executive"
#define AppExe "JARVIS-Executive.exe"
[Setup]
AppId={{59337562-9EAC-4313-A022-44CD1A4D0F2B}
AppName={#AppName}
AppVersion=1.0.0
AppVerName={#AppName} 1.0
AppPublisher=Jarvis Project
DefaultDirName={localappdata}\Programs\JARVIS Executive
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\output
OutputBaseFilename=JARVIS-Executive-Setup
SetupIconFile=..\assets\jarvis.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
[Languages]
Name: "czech"; MessagesFile: "compiler:Languages\Czech.isl"
[Tasks]
Name: "desktopicon"; Description: "Vytvořit zástupce na ploše"; GroupDescription: "Zástupci:"
[Files]
Source: "..\dist_exec\JARVIS-Executive\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
[Icons]
Name: "{autoprograms}\{#AppName} - Jarvis"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
[Run]
Filename: "{app}\{#AppExe}"; Description: "Spustit {#AppName}"; Flags: nowait postinstall skipifsilent
