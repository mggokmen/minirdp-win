# MiniRDP (Windows)

Aynı ağdaki bir bilgisayardan (Chrome/Edge) bir Windows bilgisayarı uzaktan görüntüleyip fare ve klavyeyle kontrol etmek için küçük bir uzak masaüstü sunucusu. Bağlanan tarafa kurulum gerekmez, tarayıcı yeterlidir. Windows Home sürümlerinde de çalışır (yerleşik Uzak Masaüstü sunucusu gerekmez).

> macOS sunucusu için: [mggokmen/minirdp](https://github.com/mggokmen/minirdp)

- Birincil ekran JPEG olarak WebSocket (wss) üzerinden akar, yalnızca değişiklik olduğunda kare gönderilir. Ekran ölçeklemesinden (DPI) bağımsız olarak gerçek çözünürlükte yakalanır.
- Fare (sürükleme, sağ/orta tık, dikey/yatay tekerlek), klavye (Türkçe karakterler ve AltGr dahil), otomatik pano eşitleme, tam ekranda Alt+Tab/Win tuşu.
- Şifreli giriş (PBKDF2), hatalı denemede kilitleme, yalnızca yerel ağdan erişim, Origin/Host kontrolü.
- Kendi yerel CA'sı ile HTTPS; CA yalnızca yerel IP'ler ve `.local` adları için imza atabilecek şekilde kısıtlıdır.
- Görev Zamanlayıcı ile oturum açılınca yönetici yetkisiyle başlar; çalışırken bilgisayarın uyumasını engeller.
- Ek bağımlılık yok: fare/klavye/pano doğrudan Win32 API (ctypes) ile.

## Gereksinimler

- Windows 10/11
- [python.org](https://www.python.org/downloads/windows/) Python 3.11+ (kurulumda "Add python.exe to PATH" işaretli). Microsoft Store sürümü önerilmez.

## Kurulum

Kontrol edilecek bilgisayarda, bu klasörde:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Betik yönetici yetkisi ister ve sırasıyla şunları yapar: Python ortamını kurar, şifre sorar, sertifikaları üretir, güvenlik duvarında portu yalnızca yerel alt ağa açar ve `MiniRDP` zamanlanmış görevini oluşturup başlatır. Sonunda bağlantı adresini ve CA parmak izini yazar.

Bağlanacak bilgisayarda:

1. `https://<IP>:8765/minirdp-ca.crt` adresinden CA sertifikasını indirin (ilk seferde tarayıcı uyarısını geçin), parmak izini kurulum çıktısıyla karşılaştırın ve **Geçerli Kullanıcı → Güvenilen Kök Sertifika Yetkilileri**'ne kurun:
   ```powershell
   Import-Certificate -FilePath "$env:USERPROFILE\Downloads\minirdp-ca.crt" -CertStoreLocation Cert:\CurrentUser\Root
   ```
2. Tarayıcıyı yeniden açıp `https://<IP>:8765` adresine bağlanın.

## Yönetim

Komutlar yönetici PowerShell'de, proje klasöründe çalıştırılır.

| İş | Komut |
|---|---|
| Şifre değiştir (tüm oturumları kapatır) | `venv\Scripts\python set_password.py` |
| Yeniden başlat | `Stop-ScheduledTask MiniRDP; Start-ScheduledTask MiniRDP` |
| Durdur | `Stop-ScheduledTask MiniRDP` |
| Başlat | `Start-ScheduledTask MiniRDP` |
| Otomatik başlatmayı kaldır | `Unregister-ScheduledTask MiniRDP; Remove-NetFirewallRule -DisplayName MiniRDP` |
| Log | `Get-Content minirdp.log -Wait -Tail 50` |
| Elle (konsolda) çalıştır | `venv\Scripts\python server.py` |
| IP değiştiyse | `venv\Scripts\python scripts\make_certs.py` ve yeniden başlat (CA aynı kalır) |

Ayarlar `config.json` içindedir: `port`, `quality`, `max_width`, `resolution` (bağlanınca birincil ekrana uygulanacak çözünürlük, ör. `[1920, 1080]`; istemiyorsanız `null`).

## Sınırlamalar

- **Kilit ekranı, oturum açma ekranı ve UAC onay pencereleri** Windows'un güvenli masaüstünde çalışır; bunlar görüntülenemez ve kontrol edilemez (bu sırada ekran donuk kalır, geri dönünce akış devam eder). Ctrl+Alt+Del gönderilemez. Uzaktan kullanılacak bilgisayarda ekranın otomatik kilitlenmesini kapatmak gerekebilir.
- Sunucu bir kullanıcı oturumu içinde çalışır; o kullanıcı oturum açmış olmalıdır.
- Yalnızca birincil ekran gösterilir.
- Klavye: Kısayollar ve özel tuşlar fiziksel tuş (tarama kodu) olarak, yazılan karakterler Unicode olarak gönderilir. İki bilgisayarda aynı klavye düzeninin kullanılması önerilir.

## Notlar

- `certs/ca.key` CA'nın gizli anahtarıdır; bu bilgisayardan dışarı çıkmamalı. Bağlanan bilgisayara yalnızca `minirdp-ca.crt` kurulur.
- Firefox Windows sertifika deposunu kullanmaz; Chrome veya Edge önerilir.
- Ağ profili "Ortak" ise güvenlik duvarı kuralı geçerli olmaz; ağı "Özel" yapın.
- Yalnızca ev/ofis yerel ağında kullanın, portu internete açmayın.
