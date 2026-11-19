; Inno Setup — instalacka pro J.A.R.V.I.S.
#define AppName "J.A.R.V.I.S."
#define AppExe "JARVIS.exe"
[Setup]
AppId={{8855C274-FC4F-47ED-B8E5-06D23337E166}
AppName={#AppName}
AppVersion=1.0.0
AppVerName={#AppName} 1.0
AppPublisher=Jarvis Project
DefaultDirName={localappdata}\Programs\JARVIS
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\output
OutputBaseFilename=JARVIS-Setup
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
Source: "..\dist_home\JARVIS\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
[Icons]
Name: "{autoprograms}\{#AppName} - Jarvis"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
[Run]
Filename: "{app}\{#AppExe}"; Description: "Spustit {#AppName}"; Flags: nowait postinstall skipifsilent
