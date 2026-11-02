param()
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:QT_API = 'pyside6'
$Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$VersionFile = Join-Path $PSScriptRoot 'assets\windows-version.txt'
$Icon = Join-Path $PSScriptRoot 'build\WorkBox.ico'
$PortableBuildRoot = Join-Path $PSScriptRoot '.local\portable-build\WorkBox'
$PortableRuntimeRoot = Join-Path $PSScriptRoot 'dist\total\WorkBox'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $PSScriptRoot '.local\pyinstaller-cache'
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Create .venv and install requirements-lock.txt before building.'
}
& $Python -m scripts.run_tests -q --junitxml=.local/final-tests.xml
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; build stopped.' }
& $Python -m scripts.prepare_build
if ($LASTEXITCODE -ne 0) { throw 'Build asset preparation failed.' }
if (Test-Path -LiteralPath (Join-Path $PortableBuildRoot 'data')) { throw 'The temporary build folder contains runtime data.' }
& $Python -m PyInstaller --noconfirm --clean --onedir --windowed --noupx `
    --name WorkBox --icon $Icon --version-file $VersionFile `
    --hidden-import qdarkstyle.dark.darkstyle_rc --hidden-import qdarkstyle.light.lightstyle_rc `
    --collect-data qfluentwidgets --collect-data qframelesswindow `
    --specpath build --workpath build/pyinstaller --distpath .local/portable-build main.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
if (-not (Test-Path -LiteralPath (Join-Path $PortableBuildRoot 'WorkBox.exe'))) { throw 'Missing executable.' }
& $Python -m scripts.prepare_portable_release $PortableBuildRoot
if ($LASTEXITCODE -ne 0) { throw 'Release identity verification failed.' }
$PortableReport = Join-Path $PSScriptRoot '.local\frozen-portable-check.json'
$PortableProcess = Start-Process -FilePath (Join-Path $PortableBuildRoot 'WorkBox.exe') `
    -ArgumentList @('--verify-portable', ('"' + $PortableReport + '"')) -WindowStyle Hidden -PassThru -Wait
if ($PortableProcess.ExitCode -ne 0) { throw 'Frozen application verification failed; see .local/frozen-portable-check.json.' }
& $Python -m scripts.package_portable $PortableReport --build-dir $PortableBuildRoot
if ($LASTEXITCODE -ne 0) { throw 'Portable packaging failed.' }
$RunningPortable = @(Get-Process -Name WorkBox -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -eq (Join-Path $PortableRuntimeRoot 'WorkBox.exe') })
if ($RunningPortable.Count -gt 0) { throw 'Exit BOX before updating it. The verified portable ZIP is ready; current data is untouched.' }
New-Item -ItemType Directory -Path $PortableRuntimeRoot -Force | Out-Null
Get-ChildItem -LiteralPath $PortableBuildRoot | ForEach-Object {
    if ($_.Name -eq 'data') { throw 'Never deploy build data.' }
    Copy-Item -LiteralPath $_.FullName -Destination $PortableRuntimeRoot -Recurse -Force
}
Write-Output 'Portable build complete: dist\BOX-Portable-20261029-Total-x64.zip'
