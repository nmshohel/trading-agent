<#
.SYNOPSIS
  Create a compressed PostgreSQL custom-format backup with retention cleanup.
#>

param(
  [string]$ContainerName = "trading-agent-postgres",
  [string]$DbUser = "trading_agent",
  [string]$DbName = "trading_agent",
  [string]$BackupDir = "D:\AI-Project\trading-agent\backups",
  [int]$RetentionDays = 14
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

$Stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$OutFile = Join-Path $BackupDir "trading_agent_$Stamp.dump"

Write-Host "Creating PostgreSQL backup -> $OutFile"
docker exec $ContainerName pg_dump -U $DbUser -d $DbName -Fc > $OutFile
if ($LASTEXITCODE -ne 0) {
  throw "pg_dump failed with exit code $LASTEXITCODE"
}

$Cutoff = (Get-Date).AddDays(-$RetentionDays)
Get-ChildItem -Path $BackupDir -Filter "trading_agent_*.dump" |
  Where-Object { $_.LastWriteTime -lt $Cutoff } |
  Remove-Item -Force

Write-Host "Backup complete: $OutFile"
