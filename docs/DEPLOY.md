# Deploy — Oracle Cloud (Always Free) + DuckDNS + Docker

## 0. Lokal tayyorgarlik
```bash
cp .env.example .env            # GEMINI_API_KEY va DOMAIN ni to'ldiring
.venv/bin/python -I pipeline/build_db.py   # data/uzex.duckdb (80 MB)
.venv/bin/python -I -m pytest tests -q
```

## 1. Oracle VM
- Shape: VM.Standard.A1.Flex (ARM), 2 OCPU / 12 GB yetarli. Ubuntu 22.04/24.04.
- VCN Security List → Ingress: TCP 80, 443 (0.0.0.0/0). SSH 22 bor.
- VM ichida Ubuntu firewall (iptables) ham ochiladi:
```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```
- Docker:
```bash
curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker $USER && newgrp docker
```

## 2. DuckDNS (tekin domen)
duckdns.org → login → subdomain (masalan `uzex-ai`) → IP = VM public IP. `.env` da `DOMAIN=uzex-ai.duckdns.org`.
Zaxira: domen bo'lmasa `DOMAIN=app.<ip>.sslip.io`.

## 3. Kodni va bazani yuborish
```bash
# lokal
rsync -az --exclude .venv --exclude uzex_spot_data --exclude .git ./ ubuntu@<ip>:~/uzex-ai/
# data/uzex.duckdb rsync ichida ketadi (data/ papka). Agar katta bo'lsa alohida:
# scp data/uzex.duckdb ubuntu@<ip>:~/uzex-ai/data/
```

## 4. Ishga tushirish
```bash
ssh ubuntu@<ip>
cd ~/uzex-ai && docker compose up -d --build
docker compose logs -f api        # "Uvicorn running" ko'rinsin
curl -s https://$DOMAIN/api/health
```
Caddy Let's Encrypt sertifikatni avtomatik oladi (80/443 ochiq bo'lishi shart).

## 5. Yangilash
```bash
rsync ... && ssh ubuntu@<ip> "cd ~/uzex-ai && docker compose up -d --build"
```

## 6. Azure zaxira
Xuddi shu qadamlar (B2s, Ubuntu, 80/443 NSG). Oracle yiqilsa DuckDNS da IP ni Azure IP ga almashtiring (TTL 60 s).

## 7. Monitoring
- `docker compose ps`, `docker compose logs --tail 100 api`
- Savol-javob logi: `data/logs/qa.jsonl` (hakamlar nima so'raganini ko'rish uchun)
- Tashqi uptime: uptimerobot.com (tekin) → `https://$DOMAIN/api/health` har 5 daqiqa.

## Xavfsizlik
- `.env` hech qachon git ga kirmaydi. Kalitni jamoa chatiga tashlamang.
- Baza read-only ochiladi, `enable_external_access=false`; SQL guard faqat bitta SELECT ga ruxsat beradi.
