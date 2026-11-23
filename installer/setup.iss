; ==============================================================================
; Rainy - Inno Setup Packaging Script
; ==============================================================================
; Produces a standalone, signed Windows installer from dist_nuitka\main.dist\
; ==============================================================================

#define MyAppName "Rainy"
#define MyAppVersion "1.3.0"
#define MyAppPublisher "Canefly"
#define MyAppURL "https://github.com/canefly/Rainy"
#define MyAppExeName "Rainy.exe"
#define SourceDist "..\\dist_nuitka\\main.dist"

[Setup]
AppId={{0A22D4BD-DF6A-45DC-85D7-4C0A87D84F70}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=agreement.txt
InfoBeforeFile=what_rainy_needs.txt
OutputDir=..\\dist
OutputBaseFilename=RainySetup_v{#MyAppVersion}
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

[Messages]
WizardLicense=Approval and your information
LicenseLabel=Please read how Rainy approves editors and what it sends.
LicenseLabel3=Please read the following. To install Rainy you need to agree to it.
LicenseAccepted=I &understand and agree
LicenseNotAccepted=I &do not agree
WizardInfoBefore=What Rainy needs
InfoBeforeLabel=Please read this before you continue.
InfoBeforeClickLabel=When you are ready to continue with Setup, click Next.

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
; Register .rainyproj file extension association
Root: HKA; Subkey: "Software\Classes\.rainyproj"; ValueType: string; ValueName: ""; ValueData: "Rainy.Project"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Rainy.Project"; ValueType: string; ValueName: ""; ValueData: "Rainy Project File"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Rainy.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\assets\logo.ico,0"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Rainy.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
