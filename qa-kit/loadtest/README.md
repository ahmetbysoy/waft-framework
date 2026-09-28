# `qa-kit/loadtest` — çok bağlamlı yük testi kiti

Kendi mikro-görev platformunuzun (staging) **kayıt → form doldurma → e-posta doğrulama** akışını
10 eşzamanlı tarayıcı bağlamıyla çalıştırır ve hem uygulamanızın hem WAF'ınızın bu trafiği nasıl
karşıladığını ölçer. Playwright tabanlıdır; hiçbir üçüncü parti adres gömülü değildir, CAPTCHA
**çözülmez** (yalnızca tespit edilir), kapsam kapısı koşudan önce zorlanır.

## Dosyalar

| Dosya | Rol |
|---|---|
| `credentials_pool.json` | **ŞABLON** — 10 hesap: `email`, `app_password`, `imap_host`, `imap_port` (+ opsiyonel `imap_ssl`, `enabled`, `note`) |
| `credentials_pool.sandbox.json` | Yerel devmail hesapları (127.0.0.1:1430, TLS yok) |
| `test_targets.json` | **ŞABLON** — `{TARGET_URL}/register`, `scenario: "email-verify"`, `form_fields` (`{email}`/`{password}`), `success_selector`, `verification_subject_regex` |
| `test_targets.sandbox.json` | Yerel sandbox satırları: `kayit-formu` + `captcha-drill` |
| `selectors.json` | Alan başına **sıralı, ≥3 yedekli** CSS/XPath zincirleri + `success` / `error` / `captcha_frame` |
| `run_load_test.py` | Yürütücü motor (tip belirtimli, exit kodlu, artefakt üretir) |

## Hızlı başlangıç

### A) Yerel sandbox (kimlik bilgisi gerekmez)

```bash
# 1) mock hedef (8090) + posta sunucusu (SMTP 1025 / IMAP 1430)
bash qa-kit/offerwall/sandbox_up.sh --keep

# 2) 10 bağlam / 10 paralel koşu
python3 qa-kit/loadtest/run_load_test.py --sandbox
```

### B) Kendi staging'iniz

```bash
export TARGET_URL=https://staging.sirketiniz.com          # ZORUNLU: hedef yalnızca buradan gelir
cp qa-kit/loadtest/credentials_pool.json credentials_pool.local.json
$EDITOR credentials_pool.local.json                       # 16 haneli uygulama şifreleri

# host'unuzu kapsam dosyasına ekleyin (aksi halde koşu exit 2 ile durur)
echo "staging.sirketiniz.com" >> qa-kit/authorized_hosts.txt

python3 qa-kit/loadtest/run_load_test.py \
    --credentials credentials_pool.local.json \
    --targets qa-kit/loadtest/test_targets.json \
    --contexts 10 --concurrency 5 --imap-ssl auto
```

Koşmadan önce doğrulama (tarayıcı açılmaz): `python3 qa-kit/loadtest/run_load_test.py --sandbox --check`

## Ne ölçülür / nereye yazılır

`artifacts/loadtest/<run_id>/`:

| Artefakt | İçerik |
|---|---|
| `network_log.jsonl` | Her istek/yanıt: `method`, `url`, `status`, `resource_type`, `duration_ms` (hassas sorgu değerleri `***`) |
| `endpoints.json` | `method + path` kırılımı: istek sayısı, durum kodları, p50 süre — API sözleşmesi kontrolü |
| `steps.jsonl` | Adım günlüğü: **hangi seçici tuttu** (`selector_used`), ne yazıldı (parola maskeli), zamanlar |
| `summary.json` | Makine okunur özet (bağlam/hesap/hedef bazında) |
| `report.txt` | Terminaldeki özetin aynısı |
| `screenshots/ctx-XX/` | Her adım için PNG (`--no-screenshots` ile kapatılır) |
| `loadtest.log` | Ayrıntılı log (`--log-level debug`) |

Örnek satır: `field_filled field=email selector=#register-email kind=fill` → kendi staging'inizde
markup değiştiğinde **hangi zincirin** koptuğunu doğrudan görürsünüz.

## Davranış kuralları (bilinçli sınırlar)

* **Hedef yalnızca `TARGET_URL`** — kod içinde gömülü adres yok; değişken boşsa `exit 2`.
* **Kapsam kapısı**: `qa-kit/authorized_hosts.txt` + sert 3. parti block-list. Listede olmayan host
  `--i-am-authorized` olmadan reddedilir; üçüncü parti offerwall/mikro-görev platformları **hiçbir
  koşulda** kabul edilmez.
* **Context başına 1 benzersiz hesap.** Havuzda yeterli hesap yoksa koşu başlamaz
  (`--allow-account-reuse` yalnızca açık istekle; doğrulama linki yarışı riski uyarı olarak yazılır).
* **CAPTCHA: tespit + skip.** `CAPTCHA_DETECTED` loglanır, istisna fırlatılmaz, ilgili bağlam
  güvenle kapatılır, koşu devam eder (exit 0). `--fail-on-captcha` ile kırmızı build.
* **Sırlar**: parolalar log/raporda maskeli; doğrulama linkleri ekranda `token=***` olarak görünür.
* Yer tutucu (şablon) parolalarla koşu **başlatılmaz**; `--allow-placeholders` yalnızca form
  doldurma denemesi içindir ve IMAP adımının başarısız olacağını kabul eder.

## Çıkış kodları

| Kod | Anlam |
|---|---|
| `0` | Hiç hata yok (CAPTCHA skip'leri hata sayılmaz) |
| `1` | Hedef hatası / doğrulama başarısızlığı (veya `--fail-on-captcha` + CAPTCHA) |
| `2` | Kullanım/kapsam/kimlik/yapılandırma hatası (tarayıcı hiç açılmadıysa) |
| `130` | Kullanıcı kesintisi (kısmi artefaktlar korunur) |

## Sık kullanılan bayraklar

```bash
--sandbox                      # yerel mock dosyaları + TARGET_URL=http://127.0.0.1:8090
--contexts N --concurrency M   # eşzamanlı kullanıcı sayısı / paralellik
--proxy-mode file --proxy-file proxies.txt   # hesap başına proxy (sırayla atanır)
--imap-ssl auto|on|off         # varsayılan auto: hesabın kendi değeri → port sezgisi
--imap-timeout 180 --imap-subject-regex "(doğrula|verify|confirm)"
--fail-on-captcha --no-screenshots --headful --log-level debug --no-color
```

## Doğrulanmış koşular (bu repoda)

```text
# 10 bağlam / 10 paralel, kayıt akışı:  10 ok | doğrulama 10 ok | 200×40 | exit 0
# CAPTCHA drill dahil:                  20 hedef (ok=10, captcha_detected=10) | exit 0
```

Ayrıntılı kanıt ve olumsuz yol testleri: `qa-kit/offerwall/RUNBOOK.md` §19.

## Testler

```bash
python3 -m pytest tests/test_loadtest_kit.py -q     # 20 birim testi (tarayıcı açmaz)
```
