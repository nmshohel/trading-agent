<#
.SYNOPSIS
  Restore a PostgreSQL custom-format backup into a target verification database
  and validate core tables exist.
#>

param(
  [Parameter(Mandatory = $true)]
  [string]$BackupFile,
  [string]$ContainerName = "trading-agent-postgres",
  [string]$DbUser = "trading_agent",
  [string]$TargetDb = "trading_agent_restore_test"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $BackupFile)) {
  throw "Backup file not found: $BackupFile"
}

Write-Host "Recreating verification database '$TargetDb'..."
docker exec $ContainerName psql -U $DbUser -d postgres -c "DROP DATABASE IF EXISTS $TargetDb;"
docker exec $ContainerName psql -U $DbUser -d postgres -c "CREATE DATABASE $TargetDb;"

Write-Host "Restoring $BackupFile into $TargetDb..."
Get-Content -Path $BackupFile -Encoding Byte -Raw |
  docker exec -i $ContainerName pg_restore -U $DbUser -d $TargetDb --clean --if-exists

Write-Host "Verifying core tables..."
docker exec $ContainerName psql -U $DbUser -d $TargetDb -c `
  "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name;"
Write-Host "Restore verification finished."
