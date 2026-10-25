param([string]$KiCadVersion = '10.0')
$ErrorActionPreference = 'Stop'
if ($KiCadVersion -notin @('9.0', '10.0')) { throw 'This package supports KiCad 9.0 and 10.0 only.' }
$metadata = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'plugin\metadata.json') -Raw | ConvertFrom-Json
$archivePath = Join-Path $PSScriptRoot ("dist\kistock-" + $metadata.versions[0].version + '.zip')
if (-not (Test-Path -LiteralPath $archivePath)) { throw 'Build the plugin first: python tools\build_plugin.py' }
$pluginRoot = Join-Path $env:APPDATA "kicad\$KiCadVersion\scripting\plugins"
$destination = [IO.Path]::GetFullPath((Join-Path $pluginRoot 'inventree_bom_checker'))
$pcmPath = Join-Path ([Environment]::GetFolderPath('MyDocuments')) "KiCad\$KiCadVersion\3rdparty\plugins\local_inventree-bom-checker"
if (Test-Path -LiteralPath $pcmPath) { throw 'A PCM installation exists. Update it through Plugin and Content Manager to avoid duplicate plugins.' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
try {
    foreach ($entry in $archive.Entries) {
        if (-not $entry.FullName.StartsWith('plugins/')) { continue }
        $relative = $entry.FullName.Substring(8).Replace('/', [IO.Path]::DirectorySeparatorChar)
        if (-not $relative -or $entry.FullName.EndsWith('/')) { continue }
        $target = [IO.Path]::GetFullPath((Join-Path $destination $relative))
        if (-not $target.StartsWith($destination + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe archive path.' }
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
    }
} finally { $archive.Dispose() }
Write-Host "Installed: $destination"
Write-Host 'Restart the PCB Editor. Open Tools > External Plugins > KiStock.'
