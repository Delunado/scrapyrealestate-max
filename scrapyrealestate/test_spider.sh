#!/usr/bin/env bash
#
# test_spider.sh - Prueba end-to-end de un spider concreto.
#
# Lanza un crawl real (scrapy-playwright + Chromium), guarda el resultado en
# ./data/test_<spider>.json y clasifica claramente el resultado. Es una herramienta
# manual y opt-in: nunca se ejecuta como parte de los tests offline.
#
# Ejecutar desde la carpeta que contiene scrapy.cfg (esta misma).
# En Docker: docker exec -it <contenedor> bash -c "cd /scrapyrealestate/scrapyrealestate && ./test_spider.sh <spider> [url]"
#
# Uso: ./test_spider.sh <spider> [url]
#   idealista | pisoscom | habitaclia | fotocasa | yaencontre

set -u

SPIDER="${1:-fotocasa}"

case "$SPIDER" in
  idealista)  DEFAULT_URL="https://www.idealista.com/alquiler-viviendas/madrid-madrid/?ordenado-por=fecha-publicacion-desc" ;;
  pisoscom)   DEFAULT_URL="https://www.pisos.com/venta/pisos-madrid/fecharecientedesde-desc/" ;;
  habitaclia) DEFAULT_URL="https://www.habitaclia.com/alquiler-madrid.htm?ordenar=mas_recientes" ;;
  fotocasa)   DEFAULT_URL="https://www.fotocasa.es/es/alquiler/viviendas/madrid-capital/todas-las-zonas/l" ;;
  yaencontre) DEFAULT_URL="https://www.yaencontre.com/alquiler/pisos/madrid/o-recientes" ;;
  *) echo "Spider desconocido: $SPIDER (usa: idealista | pisoscom | habitaclia | fotocasa | yaencontre)"; exit 1 ;;
esac

URL="${2:-$DEFAULT_URL}"
DATA_DIR="$(python3 -m scrapyrealestate.runtime)"
OUT="${DATA_DIR}/test_${SPIDER}.json"
LOG="${DATA_DIR}/test_${SPIDER}.log"

if [ ! -f "scrapy.cfg" ]; then
  echo "ERROR: ejecuta este script desde la carpeta que contiene scrapy.cfg."
  exit 1
fi

# El settings.py lee el User-Agent del directorio de datos compartido.
mkdir -p "$DATA_DIR"
if [ ! -s "${DATA_DIR}/useragent.txt" ]; then
  echo "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" > "${DATA_DIR}/useragent.txt"
fi

rm -f "$OUT" "$LOG"

echo "=================================================================="
echo " Spider : $SPIDER"
echo " URL    : $URL"
echo " Salida : $OUT"
echo " Log    : $LOG"
echo "=================================================================="
echo ">> Lanzando crawl (logs en nivel INFO)..."
echo

scrapy crawl -L INFO "$SPIDER" -o "$OUT" -a start_urls="$URL" 2>&1 | tee "$LOG"
STATUS=${PIPESTATUS[0]}

echo
echo "=================================================================="
python3 -m scrapyrealestate.live_spider_result \
  --output "$OUT" \
  --log "$LOG" \
  --crawl-exit-code "$STATUS"
RESULT_STATUS=$?
echo "=================================================================="
exit "$RESULT_STATUS"
