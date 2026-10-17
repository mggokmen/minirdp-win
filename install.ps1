# MiniRDP kurulumu: Python ortamı, şifre, sertifikalar, güvenlik duvarı kuralı ve oturum açılınca otomatik başlatma.
# Kullanım (PowerShell):  powershell -ExecutionPolicy Bypass -File .\install.ps1
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$Dir = $PSScriptRoot

# Güvenlik duvarı kuralı ve yönetici pencerelerini kontrol edebilen görev için yönetici yetkisi gerekir.
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host "Yönetici yetkisi isteniyor..."
    Start-Process powershell -Verb RunAs -ArgumentList "-NoExit -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

Write-Host "== Python ortamı"
$py = $null
# Python Install Manager da Store yönlendiricisi de WindowsApps altındadır; ayırt etmek için sürümü sorulur
# (yönlendirici sürüm yazmaz).
foreach ($c in @(@('py'), @('python'))) {
    if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
    $ver = & $c[0] -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null | Select-Object -Last 1
    if ($ver -match '^\d+\.\d+$' -and [version]$ver -ge [version]'3.11') { $py = $c; break }
}
if (-not $py) { throw "Python 3.11+ bulunamadı. python.org'dan Python Install Manager ile kurun." }
if (-not (Test-Path venv)) { & $py[0] $py[1..9] -m venv venv }
$vpy = "$Dir\venv\Scripts\python.exe"
& $vpy -m pip install -q --upgrade pip
& $vpy -m pip install -q -r requirements.txt
if ($LASTEXITCODE) { throw "Paketler kurulamadı." }

Write-Host "== Yapılandırma"
if (-not (Test-Path config.json)) { Copy-Item config.example.json config.json }
if (-not (Select-String -Path config.json -Pattern password_hash -Quiet)) { & $vpy set_password.py; if ($LASTEXITCODE) { throw "Şifre ayarlanmadı." } }

Write-Host "== Sertifikalar"
if (-not (Test-Path certs\server.crt)) { & $vpy scripts\make_certs.py; if ($LASTEXITCODE) { throw "Sertifika üretilemedi." } }
# Özel anahtarlara yalnızca bu kullanıcı, SYSTEM ve yöneticiler erişebilsin.
icacls certs /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
icacls config.json /inheritance:r /grant:r "${env:USERNAME}:F" "SYSTEM:F" "*S-1-5-32-544:F" | Out-Null

$port = (Get-Content config.json -Raw | ConvertFrom-Json).port
Write-Host "== Güvenlik duvarı (TCP $port, yalnızca yerel alt ağ)"
Get-NetFirewallRule -DisplayName MiniRDP -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName MiniRDP -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port `
    -RemoteAddress LocalSubnet -Profile Private, Domain | Out-Null

Write-Host "== Otomatik başlatma (Görev Zamanlayıcı: MiniRDP)"
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute "$Dir\venv\Scripts\pythonw.exe" -Argument "`"$Dir\server.py`"" -WorkingDirectory $Dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName MiniRDP -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Stop-ScheduledTask MiniRDP -ErrorAction SilentlyContinue
Start-ScheduledTask MiniRDP

$ip = (Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | Select-Object -First 1).IPv4Address.IPAddress
$net = (Get-NetConnectionProfile | Select-Object -First 1).NetworkCategory
$thumb = (New-Object System.Security.Cryptography.X509Certificates.X509Certificate2("$Dir\certs\minirdp-ca.crt")).Thumbprint
Write-Host ""
Write-Host "Kurulum tamam. MiniRDP çalışıyor: https://${ip}:$port"
if ($net -eq 'Public') { Write-Host "UYARI: Ağ profili 'Ortak' - güvenlik duvarı kuralı yalnızca 'Özel' ağda geçerli. Ayarlar > Ağ'dan 'Özel' yapın." -ForegroundColor Yellow }
Write-Host "Bağlanacak bilgisayarda:"
Write-Host " 1. https://${ip}:$port/minirdp-ca.crt adresinden CA'yı indirin (ilk seferde tarayıcı uyarısını geçin),"
Write-Host "    parmak izinin $thumb olduğunu doğrulayıp kurun:"
Write-Host "    Import-Certificate -FilePath `"`$env:USERPROFILE\Downloads\minirdp-ca.crt`" -CertStoreLocation Cert:\CurrentUser\Root"
Write-Host " 2. Tarayıcıyı yeniden açıp https://${ip}:$port adresine bağlanın."
