# ==============================================================================
# Rainy - Nuitka Compilation Ritual Script
# ==============================================================================
# Automates the complete standalone build process for Rainy.exe
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\build_nuitka.ps1
# ==============================================================================

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

Set-Location $ProjectRoot
Write-Host ">>> Starting Rainy Nuitka Build Ritual..." -ForegroundColor Cyan

# 1. Check Python Venv
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtual environment not found at $PythonExe. Please initialize .venv first."
}

# 2. Apply / Verify Nuitka Patches
$SitePackages = Join-Path $ProjectRoot ".venv\Lib\site-packages"

# Patch 1: compiled_coroutine.h (Universal int typedef for Await_Kind to fix C++ mangling link error)
$CoroHeader = Join-Path $SitePackages "nuitka\build\include\nuitka\compiled_coroutine.h"
if (Test-Path $CoroHeader) {
    $content = Get-Content $CoroHeader -Raw
    if ($content -match 'enum Await_Kind') {
        Write-Host ">>> Patching compiled_coroutine.h for MSVC C++ name mangling..." -ForegroundColor Yellow
        $patched = $content -replace '#ifdef __cplusplus\s+enum Await_Kind\s+#else\s+typedef int Await_Kind;\s+#endif', 'typedef int Await_Kind;'
        Set-Content -Path $CoroHeader -Value $patched -NoNewline
    }
}

# Patch 2: SourceHandling.py (Allow incremental re-generation without assertion error)
$SourceHandling = Join-Path $SitePackages "nuitka\tree\SourceHandling.py"
if (Test-Path $SourceHandling) {
    $content = Get-Content $SourceHandling -Raw
    if ($content -match 'assert not os\.path\.isfile\(filename\), filename') {
        Write-Host ">>> Patching SourceHandling.py for incremental source overwrite..." -ForegroundColor Yellow
        $patched = $content -replace 'assert not os\.path\.isfile\(filename\), filename', '# assert not os.path.isfile(filename), filename'
        Set-Content -Path $SourceHandling -Value $patched -NoNewline
    }
}

# Patch 3: MetaPathBasedLoaderResourceReader.c (Guard duplicate declaration under MSVC)
$MetaReader = Join-Path $SitePackages "nuitka\build\static_src\MetaPathBasedLoaderResourceReader.c"
if (Test-Path $MetaReader) {
    $content = Get-Content $MetaReader -Raw
    if ($content -notmatch '__IDE_ONLY__') {
        Write-Host ">>> Patching MetaPathBasedLoaderResourceReader.c for duplicate declaration..." -ForegroundColor Yellow
        $patched = $content -replace 'extern PyObject \*\s*_kw_list_get_data\(PyObject \*kw_list, Py_ssize_t index\);', "#ifdef __IDE_ONLY__`nextern PyObject *`n_kw_list_get_data(PyObject *kw_list, Py_ssize_t index);`n#endif"
        Set-Content -Path $MetaReader -Value $patched -NoNewline
    }
}

# 3. Clean any orphaned compiler or running application instances
Get-Process -Name "Rainy", "cl" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

# 4. Run Nuitka Compilation
Write-Host ">>> Invoking Nuitka compiler..." -ForegroundColor Green
& $PythonExe -m nuitka `
    --standalone `
    --assume-yes-for-downloads `
    --windows-console-mode=disable `
    --enable-plugin=pyside6 `
    --include-package=qfluentwidgets `
    --include-package-data=qfluentwidgets `
    --include-package=docx `
    --include-package-data=docx `
    --include-data-dir=assets=assets `
    --windows-icon-from-ico=assets/logo.ico `
    --output-dir=dist_nuitka `
    --output-filename=Rainy.exe `
    src/main.py

if ($LASTEXITCODE -ne 0) {
    Write-Error "Nuitka compilation failed with exit code $LASTEXITCODE"
}

# 5. Engines (libmpv, FFmpeg, FFprobe) are NOT bundled any more. After the license check passes the app
#    downloads them once into %LOCALAPPDATA%\com.canefly.knot\bin from the files the server has pinned
#    (see docs/app-folder.md). The repo's bin\ folder is only for running from source.
Write-Host ">>> Not bundling engines: the app downloads and verifies them on first run." -ForegroundColor Cyan

$ExePath = Join-Path $ProjectRoot "dist_nuitka\main.dist\Rainy.exe"
if (Test-Path $ExePath) {
    $item = Get-Item $ExePath
    Write-Host "=================================================================" -ForegroundColor Green
    Write-Host " SUCCESS: Rainy.exe compiled and bundled successfully!" -ForegroundColor Green
    Write-Host " Location : $ExePath" -ForegroundColor White
    Write-Host " Size     : $([math]::Round($item.Length / 1MB, 2)) MB" -ForegroundColor White
    Write-Host "=================================================================" -ForegroundColor Green
} else {
    Write-Error "Target executable $ExePath not found after build!"
}
