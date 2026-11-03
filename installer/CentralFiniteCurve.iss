; Inno Setup script for Central Finite Curve (Windows installer).
; Build:  ISCC.exe installer\CentralFiniteCurve.iss
; Override version / source at build time, e.g.:
;   ISCC.exe /DMyAppVersion=0.1.0-alpha /DSourceDir=..\dist\CentralFiniteCurve installer\CentralFiniteCurve.iss
; publish.ps1 passes these automatically.

#define MyAppName "Central Finite Curve"
#define MyAppPublisher "DaGromflomite"
#define MyAppExeName "CentralFiniteCurve.exe"
#define MyAppURL "https://github.com/DaGromflomite/Central-Finite-Curve"

#ifndef MyAppVersion
  #define MyAppVersion "0.1.0-alpha"
#endif
; Folder produced by PyInstaller (onedir). Relative to this .iss file by default.
#ifndef SourceDir
  #define SourceDir "..\dist\CentralFiniteCurve"
#endif
; Where the finished Setup .exe is written.
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
; Stable AppId so upgrades/uninstall find the previous install. Do not change.
AppId={{7B3C9E2A-1F4D-4C8B-9A6E-2D5F8C1E4B70}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\Central Finite Curve
DefaultGroupName=Central Finite Curve
DisableProgramGroupPage=yes
; Per-user install by default -> no admin prompt; user may elevate for all-users.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=CentralFiniteCurve-Setup-{#MyAppVersion}
SetupIconFile=..\assets\CentralFiniteCurve.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
