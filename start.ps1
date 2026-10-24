param([string]$InputBom)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $InputBom) {
    Add-Type -AssemblyName System.Windows.Forms
    $picker = New-Object System.Windows.Forms.OpenFileDialog
    $picker.Title = 'Select a KiCad BOM or iBOM'
    $picker.Filter = 'BOM KiCad / iBOM (*.csv;*.html;*.xml)|*.csv;*.html;*.xml'
    if ($picker.ShowDialog() -ne 'OK') { exit 0 }
    $InputBom = $picker.FileName
}
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'config.json'))) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'config.example.json') -Destination (Join-Path $PSScriptRoot 'config.json')
}
$temporaryToken = -not $env:INVENTREE_TOKEN
try {
    if ($temporaryToken) {
        Write-Host 'InvenTree API token (read-only access, used only for this session):'
        $secureToken = Read-Host -AsSecureString
        $tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
        try { $env:INVENTREE_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer) }
    }
    $reportDirectory = Join-Path $PSScriptRoot 'reports'
    New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
    $reportBase = Join-Path $reportDirectory ([IO.Path]::GetFileNameWithoutExtension($InputBom) + '-stock')
    & python (Join-Path $PSScriptRoot 'bom_checker.py') $InputBom $reportBase --open
    $resultCode = $LASTEXITCODE
}
finally {
    if ($temporaryToken) { Remove-Item Env:\INVENTREE_TOKEN -ErrorAction SilentlyContinue }
}
exit $resultCode
