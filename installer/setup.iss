; ==============================================================================
; Clickables Tools Native - Inno Setup Packaging Script
; ==============================================================================
; Produces a standalone, signed Windows installer from dist_nuitka\main.dist\
; ==============================================================================

#define MyAppName "Clickables Tools"
#define MyAppVersion "1.2.0"
#define MyAppPublisher "Canefly"
#define MyAppURL "https://github.com/canefly/clickables-tool-native"
#define MyAppExeName "ClickablesParser.exe"
#define SourceDist "..\\dist_nuitka\\main.dist"

[Setup]
AppId={{9F82A4D1-5C9B-4D56-829E-3C5D8B49071E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=
OutputDir=..\\dist
OutputBaseFilename=ClickablesTools_Setup_v{#MyAppVersion}
SetupIconFile=..\\assets\\logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Standalone compiled application files and dependencies
Source: "{#SourceDist}\*"; DestDir: "{app}"; Excludes: "*.zip"; Flags: ignoreversion recursesubdirs createallsubdirs
; Logo icon for shortcuts and uninstall registration
Source: "..\\assets\\logo.ico"; DestDir: "{app}\\assets"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Register .clickproj file extension association
Root: HKA; Subkey: "Software\Classes\.clickproj"; ValueType: string; ValueName: ""; ValueData: "Clickables.Project"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Clickables.Project"; ValueType: string; ValueName: ""; ValueData: "Clickables Project File"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Clickables.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\assets\logo.ico,0"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Clickables.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
