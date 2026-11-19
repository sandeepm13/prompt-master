; Inno Setup script - makes dist\MasterPrompt-Setup-<version>.exe
; Build it with build-exe.bat (which passes the version number in).
; Installs per user (no admin rights needed) into %LOCALAPPDATA%\Programs\Master Prompt.
; Settings + API key live in %APPDATA%\MasterPrompt and are kept on uninstall/upgrade.

#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif

[Setup]
AppId={{6F3A2C1E-8B4D-4E7A-9C2F-5D1B7A9E3C40}
AppName=dotix
AppVersion={#AppVersion}
AppPublisher=Sandeep Mopuri
AppComments=dotix - prompt magic across all your apps.
DefaultDirName={localappdata}\Programs\dotix
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=dotix-Setup-{#AppVersion}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\MasterPrompt.exe
UninstallDisplayName=dotix
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "startup"; Description: "Start dotix automatically when I log in to Windows (recommended)"
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "dist\MasterPrompt\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\dotix"; Filename: "{app}\MasterPrompt.exe"
Name: "{userstartup}\dotix"; Filename: "{app}\MasterPrompt.exe"; Tasks: startup
Name: "{userdesktop}\dotix"; Filename: "{app}\MasterPrompt.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MasterPrompt.exe"; Description: "Start dotix now"; Flags: nowait postinstall

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM MasterPrompt.exe"; Flags: runhidden; RunOnceId: "StopApp"

[Code]
// Stop a running copy first, so an upgrade can replace MasterPrompt.exe.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM MasterPrompt.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;
