; Per-user installer for Photo Finder. Build it with build.ps1, not ISCC alone.
#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif

#define AppName "Photo Finder"
#define AppExe "PhotoFinder.exe"
#define Payload "build\payload"

[Setup]
AppId={{B0F5D3C2-3B7C-4F1E-9F2A-6D8C4E7A1B90}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
AppPublisherURL=https://github.com/eggs12345678/photo-finder
AppSupportURL=https://github.com/eggs12345678/photo-finder
DefaultDirName={localappdata}\Programs\PhotoFinder
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=dist
OutputBaseFilename=PhotoFinder-{#AppVersion}-setup
SetupIconFile=build\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
ChangesAssociations=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[InstallDelete]
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\web"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\assets"

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}\app"; IconFilename: "{app}\{#AppExe}"; Comment: "Search your photos and clips"
Name: "{group}\Photo Finder data folder"; Filename: "{localappdata}\PhotoFinder\data"; Comment: "Index, thumbnails and settings"
Name: "{group}\Stop Photo Finder"; Filename: "{app}\{#AppExe}"; Parameters: "stop"; WorkingDir: "{app}\app"; IconFilename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}\app"; IconFilename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\run.py"" stop"; WorkingDir: "{app}\app"; Flags: runhidden; RunOnceId: "StopPhotoFinder"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\web"
Type: filesandordirs; Name: "{app}\assets"

[Code]
procedure StopRunningServer();
var
  ResultCode: Integer;
begin
  try
    if FileExists(ExpandConstant('{app}\python\pythonw.exe')) and
       FileExists(ExpandConstant('{app}\app\run.py')) then
      Exec(
        ExpandConstant('{app}\python\pythonw.exe'),
        '"' + ExpandConstant('{app}\app\run.py') + '" stop',
        ExpandConstant('{app}\app'),
        SW_HIDE,
        ewWaitUntilTerminated,
        ResultCode);
  except
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningServer();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopRunningServer();
  Result := True;
end;
