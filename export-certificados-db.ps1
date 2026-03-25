# Requer: Windows 10+ (curl.exe incluído).
# Uso no PowerShell:
#   .\export-certificados-db.ps1 -Token 'seu_token_com_#_ou_outros_simbolos'
# Uso no CMD:
#   powershell -NoProfile -ExecutionPolicy Bypass -File export-certificados-db.ps1 -Token "seu_token"

param(
    [string] $BaseUrl = "https://meu-certificado.up.railway.app",
    [Parameter(Mandatory = $true)]
    [string] $Token,
    [ValidateSet("sqlite", "json")]
    [string] $Format = "sqlite",
    [string] $OutFile = ""
)

$ErrorActionPreference = "Stop"

$safeBase = $BaseUrl.TrimEnd("/")
$query = "export_format=$Format"
$url = "$safeBase/api/admin/export?$query"

if (-not $OutFile) {
    $OutFile = if ($Format -eq "json") { "certificados_export.json" } else { "certificados_backup.db" }
}

if (-not [System.IO.Path]::IsPathRooted($OutFile)) {
    $OutFile = Join-Path -Path (Get-Location).Path -ChildPath $OutFile
}
$OutFile = [System.IO.Path]::GetFullPath($OutFile)

Write-Host "A descarregar para: $OutFile"

$args = @(
    "-f", "-L",
    "-o", $OutFile,
    "-H", "Authorization: Bearer $Token",
    $url
)

& curl.exe @args
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "Concluído."
