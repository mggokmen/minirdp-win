# MiniRDP kurulumu: Python ortamı, şifre, sertifikalar, güvenlik duvarı kuralı ve oturum açılınca otomatik başlatma.
# Kullanım (PowerShell):  powershell -ExecutionPolicy Bypass -File .\install.ps1 [-Python C:\yol\python.exe]
param([string]$Python)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$Dir = $PSScriptRoot

# Güvenlik duvarı kuralı ve yönetici pencerelerini kontrol edebilen görev için yönetici yetkisi gerekir.
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host "Yönetici yetkisi isteniyor..."
    Start-Process powershell -Verb RunAs -ArgumentList "-NoExit -ExecutionPolicy Bypass -File `"$PSCommandPath`"" + $(if ($Python) { " -Python `"$Python`"" })
    exit
}

Write-Host "== Python ortamı"
$py = $null
# Sırayla: -Python ile verilen, Python Install Manager'ın / klasik yükleyicinin kurduğu (en yeni sürüm önce),
# PATH'teki py / python. Girdi $null'dan verilir: ilk çalıştırma soruları görünmeden beklemesin.
# Microsoft Store yönlendiricisi (WindowsApps\python.exe) hiçbir şey yazmadığı için kendiliğinden elenir.
$candidates = @()
if ($Python) { $candidates += $Python }
$candidates += Get-ChildItem "$env:LOCALAPPDATA\Python\pythoncore-3.*\python.exe", "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
    Sort-Object { [int]($_.Directory.Name -replace '^\D*3\.?(\d+).*$', '$1') } -Descending | ForEach-Object FullName
$candidates += 'py', 'python'
foreach ($c in $candidates) {
    if (-not (Get-Command $c -ErrorAction SilentlyContinue)) { continue }
    Write-Host "   '$c' deneniyor..."
    $out = $null | & $c -c "import sys;sys.version_info>=(3,11) and print(sys.executable)" 2>$null | Select-Object -Last 1
    if ($out -and (Test-Path $out)) { $py = $out; break }
}
if (-not $py) { throw "Python 3.11+ bulunamadı. python.org'dan Python Install Manager ile kurup 'py install 3.15' çalıştırın ya da yolu verin: .\install.ps1 -Python C:\yol\python.exe" }
Write-Host "   Python: $py"
if (-not (Test-Path venv\Scripts\python.exe)) {
    Write-Host "   Sanal ortam oluşturuluyor..."
    & $py -m venv venv
    if ($LASTEXITCODE) { throw "Sanal ortam oluşturulamadı." }
}
$vpy = "$Dir\venv\Scripts\python.exe"
Write-Host "   Paketler indiriliyor (bir iki dakika sürebilir)..."
& $vpy -m pip install --disable-pip-version-check -r requirements.txt
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
