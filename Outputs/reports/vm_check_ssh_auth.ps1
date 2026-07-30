$path = 'C:\ProgramData\ssh\administrators_authorized_keys'
Write-Output ('exists=' + (Test-Path -LiteralPath $path))
if (Test-Path -LiteralPath $path) {
    Write-Output ('length=' + (Get-Item -LiteralPath $path).Length)
    Write-Output ('lines=' + @(Get-Content -LiteralPath $path).Count)
    icacls $path
}
Get-Service sshd | Select-Object Name,Status,StartType | ConvertTo-Json -Compress
