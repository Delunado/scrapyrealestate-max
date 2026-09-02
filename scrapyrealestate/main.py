#!/usr/bin/python3
# -*- coding: utf-8 -*-
import re
import json
import logging
import random
import subprocess
import sys
import telebot
import time
import urllib.error
import urllib.request
from os import path
from urllib.parse import urlsplit

from art import tprint
from fake_useragent import UserAgent

from scrapyrealestate.atomic_files import atomic_write_json
from scrapyrealestate.legacy_config import (
    ConfigIssue,
    ConfigValidationError,
    LegacyConfig,
    load_legacy_config,
)
from scrapyrealestate.portals import PortalRegistry, PortalRequestError, build_default_registry
from scrapyrealestate.runtime import get_runtime_paths
from scrapyrealestate.security import (
    SecretRedactionFilter,
    SecretRedactingFormatter,
    configured_telegram_secrets,
    resolve_telegram_bot_token,
)


__license__ = "GPL"
__version__ = "3.0.0"

runtime_paths = get_runtime_paths()
data = LegacyConfig()
registry: PortalRegistry = build_default_registry()


def get_bot_token():
    return resolve_telegram_bot_token(data)

# Por si fake-useragent falla.
FALLBACK_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)


def init_logs():
    global logger
    try:
        log_level = data.log_level
    except (KeyError, NameError, AttributeError):
        log_level = 'INFO'

    levels = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR,
        'CRITICAL': logging.CRITICAL,
    }
    log_level = levels.get(log_level, logging.INFO)

    logger = logging.getLogger()
    logger.setLevel(log_level)

    ch = logging.StreamHandler()
    ch.setLevel(log_level)
    configured_secrets = configured_telegram_secrets(data)
    ch.setFormatter(
        SecretRedactingFormatter(
            '[%(asctime)s] [%(levelname)s] %(message)s',
            "%Y-%m-%d %H:%M:%S",
            secrets=configured_secrets,
        )
    )
    ch.addFilter(SecretRedactionFilter(configured_secrets))
    logger.addHandler(ch)

    return logger


def mix_list(original_list):
    # baraja para no empezar siempre por el mismo portal
    shuffled = original_list[:]
    random.shuffle(shuffled)
    return shuffled


def get_config():
    # Sin config.json arrancamos la web para que el usuario lo cree.
    if not runtime_paths.config_file.is_file():
        runtime_paths.ensure_data_dir()
        process = init_app_flask()
        get_config_flask(process)
    else:
        global data
        data = load_legacy_config(runtime_paths.config_file)


def check_config():
    if not path.exists("scrapy.cfg"):
        raise ConfigValidationError(
            [
                ConfigIssue(
                    "runtime_directory",
                    "scrapy.cfg was not found; run from the Scrapy project directory",
                )
            ]
        )

    # URLs para el mensaje de inicio.
    urls = get_urls(data)
    urls_ok = ''
    urls_ok_count = 0
    for portal in urls:
        for url in urls[portal]:
            if len(url.split('/')) > 2:
                portal_url = url.split('/')[2]
                portal_name = portal_url.split('.')[1]
                urls_ok_count += 1
                urls_ok += f' <a href="{url}">{portal_name}</a>    '

    if not data.telegram_chatuser_id:
        raise ConfigValidationError(
            [ConfigIssue("telegram_chatuserID", "must not be empty")]
        )

    tb = telebot.TeleBot(get_bot_token())

    try:
        if data.start_msg:
            info_message = tb.send_message(
                data.telegram_chatuser_id,
                f"<code>LOADING...</code>\n"
                f"\n"
                f"<code>scrapyrealestate v{__version__}\n</code>"
                f"\n"
                f"<code>REFRESH     <b>{data.time_update}</b>s</code>\n"
                f"<code>MIN PRICE   <b>{data.min_price}€</b></code>\n"
                f"<code>MAX PRICE   <b>{data.max_price}€</b> (0 = NO LIMIT)</code>\n"
                f"<code>URLS        <b>{urls_ok_count}</b>  →   </code>{urls_ok}\n",
                parse_mode='HTML'
            )
        else:
            info_message = tb.send_message(
                data.telegram_chatuser_id,
                f"LOADING... scrapyrealestate v{__version__}\n")
    except telebot.apihelper.ApiTelegramException as error:
        raise ConfigValidationError(
            [
                ConfigIssue(
                    "telegram",
                    "the chat ID or bot token could not be verified",
                )
            ]
        ) from error

    logger.info(f"CANAL DE TELEGRAM {info_message.chat.title} VERIFICADO")
    return info_message


def checks():
    if data.time_update < 300:
        raise ConfigValidationError(
            [ConfigIssue("time_update", "must be at least 300 seconds")]
        )
    check_config()   # valida la configuración y verifica el canal de Telegram


def check_url(url):
    try:
        url_code = urllib.request.urlopen(url).getcode()
    except (urllib.error.URLError, OSError):
        url_code = 404
    return url_code


def init_app_flask():
    # Devuelve el proceso (o None si el servidor ya estaba arriba) para poder pararlo.
    localhost_code = check_url("http://localhost:8080")
    if localhost_code == 200:
        return None

    python_bin = sys.executable or "python3"
    process = subprocess.Popen([python_bin, "./scrapyrealestate/flask_server.py"])
    return process


def get_config_flask(process):
    # Espera a que la web escriba config.json y para el servidor.
    global data
    while True:
        if runtime_paths.config_file.is_file():
            try:
                with runtime_paths.config_file.open(encoding="utf-8") as config_file:
                    data = LegacyConfig.from_mapping(json.load(config_file))
                break
            except json.JSONDecodeError:
                # todavía se está escribiendo
                pass
        time.sleep(1)
    if process is not None:
        process.terminate()


def get_urls(data: LegacyConfig):
    urls = {}

    if not any(data.portal_urls.values()):
        raise ConfigValidationError(
            [ConfigIssue("portal_urls", "at least one portal URL is required")]
        )

    start_urls_idealista = data.url_idealista
    start_urls_idealista = [url + '?ordenado-por=fecha-publicacion-desc' for url in start_urls_idealista]

    start_urls_pisoscom = data.url_pisoscom
    start_urls_pisoscom = [url + 'fecharecientedesde-desc/' for url in start_urls_pisoscom]

    start_urls_fotocasa = data.url_fotocasa

    start_urls_habitaclia = data.url_habitaclia
    start_urls_habitaclia = [url + '?ordenar=mas_recientes' for url in start_urls_habitaclia]

    start_urls_yaencontre = data.url_yaencontre
    start_urls_yaencontre = [url + '/o-recientes' for url in start_urls_yaencontre]

    urls['start_urls_idealista'] = start_urls_idealista
    urls['start_urls_pisoscom'] = start_urls_pisoscom
    urls['start_urls_fotocasa'] = start_urls_fotocasa
    urls['start_urls_habitaclia'] = start_urls_habitaclia
    urls['start_urls_yaencontre'] = start_urls_yaencontre

    return urls


def check_new_flats(json_file_name, scrapy_rs_name, min_price, max_price,
                    tg_chatID, telegram_msg, logger):
    '''Detecta viviendas no vistas (contra data/ids.json) y envía por Telegram
    las que entran en el rango de precio. Dedup 100% local, sin BD.'''
    tb = telebot.TeleBot(get_bot_token())
    new_urls = []

    try:
        with open(json_file_name) as json_file:
            data_json = json.load(json_file)
    except (FileNotFoundError, json.JSONDecodeError):
        data_json = []

    if len(data_json) == 0:
        logger.warning(f'SIN DATOS EN EL JSON {scrapy_rs_name.upper()}')

    try:
        with runtime_paths.ids_file.open("r") as outfile:
            ids_file = json.load(outfile)
    except (FileNotFoundError, json.JSONDecodeError):
        ids_file = []
    new_ids_file = []

    for flat in data_json:
        try:
            flat_id = int(flat['id'])
        except (KeyError, ValueError, TypeError):
            continue

        price_str = flat.get('price', '')
        href = flat.get('href', '')

        # precio a entero (solo dígitos); si no, dejamos el texto
        try:
            price = int(''.join(char for char in price_str if char.isdigit()))
        except (ValueError, TypeError):
            price = 0
        if price == 0:
            price = price_str

        # m2 a entero para el €/m²
        try:
            m2 = int(''.join(char for char in flat.get('m2', '') if char.isdigit())[:-1])
            m2_tg = f'{m2}m²'
        except (ValueError, TypeError):
            m2 = flat.get('m2', 0) or 0
            m2_tg = f'{m2}m²' if m2 else ''

        if flat_id in ids_file:
            continue
        new_ids_file.append(flat_id)

        # "A consultar": lo damos por visto pero no lo enviamos
        if price in ('Aconsultar', 'A consultar'):
            continue

        try:
            within_range = (int(max_price) >= int(price) >= int(min_price)
                            or (int(max_price) == 0 and int(price) >= int(min_price)))
        except (ValueError, TypeError):
            within_range = False

        if within_range and telegram_msg:
            new_urls.append(href)
            try:
                avg_price_m2 = '%.2f' % (price / float(m2))
            except (ValueError, ZeroDivisionError, TypeError):
                avg_price_m2 = ''
            try:
                tb.send_message(
                    tg_chatID,
                    f"<b>{price_str}</b> [{m2_tg}] → {avg_price_m2}€/m²\n{href}",
                    parse_mode='HTML')
            except telebot.apihelper.ApiTelegramException as e:
                logger.error(f'ERROR ENVIANDO A TELEGRAM: {e}')
            time.sleep(3.05)

    atomic_write_json(runtime_paths.ids_file, ids_file + new_ids_file)

    # solo a INFO si hay nuevas; si no, a DEBUG
    if new_urls:
        logger.info(f"NUEVAS: {len(new_urls)} | TOTAL: {len(data_json)} -> {new_urls}")
    else:
        logger.debug(f"NUEVAS: 0 | TOTAL: {len(data_json)}")


def run_spider(spider_name, scrapy_log, out_file, start_url):
    # Lista de args (sin shell): las URLs con '?'/'&' no rompen la línea de comandos.
    cmd = ["scrapy", "crawl", "-L", scrapy_log, spider_name,
           "-o", out_file, "-a", f"start_urls={start_url}"]
    subprocess.run(cmd, check=False)


def scrap_realestate(telegram_msg):
    scrapy_rs_name = data.scrapy_rs_name.replace("-", "_")
    scrapy_log = data.log_level_scrapy
    out_file = str(runtime_paths.crawl_output(scrapy_rs_name))

    # todas las claves 'url_*' de la config
    urls = []
    for portal_urls in data.portal_urls.values():
        urls.extend(portal_urls)

    urls_mixed = mix_list(urls)

    process = subprocess.run(["scrapy", "list"], capture_output=True)
    if process.returncode != 0:
        raise RuntimeError("Scrapy could not discover the configured spiders")

    for url in urls_mixed:
        if url == '':
            continue

        hostname = urlsplit(url).hostname or ''
        try:
            adapter = registry.get_by_hostname(hostname)
            request = adapter.build_request(url)
        except (KeyError, PortalRequestError) as error:
            logger.warning(f"SKIPPING UNRECOGNIZED OR INVALID URL {url!r}: {error}")
            continue

        portal_name = adapter.metadata.display_name
        logger.debug(f"SCRAPING PORTAL {portal_name} FROM {scrapy_rs_name}...")
        run_spider(request.spider_name, scrapy_log, out_file, request.start_url)
        logger.debug(f"CRAWLED {portal_name.upper()}")

    # Scrapy con -o concatena varios crawls en el mismo fichero; unimos las partes ('][').
    logger.debug(f"EDITING {out_file}...")
    try:
        with open(out_file, 'r') as file:
            filedata = file.read()
    except FileNotFoundError:
        logger.warning(f"NO SE GENERÓ {out_file} (NINGÚN RESULTADO)")
        return

    filedata = filedata.replace('\n][', ',')
    filedata = re.sub('\n,\n', '', filedata)
    filedata = re.sub(',\n\n', '', filedata)
    filedata = re.sub(',\n]', ']', filedata)
    with open(out_file, 'w') as file:
        file.write(filedata)

    check_new_flats(out_file,
                    scrapy_rs_name,
                    data.min_price,
                    data.max_price,
                    data.telegram_chatuser_id,
                    telegram_msg,
                    logger)


def update_useragent():
    try:
        runtime_paths.user_agent_file.unlink()
    except FileNotFoundError:
        pass
    try:
        ua = UserAgent(platforms='pc', os=['windows', 'macos'])
        useragent = ua.chrome
    except Exception as e:
        logger.warning(f'fake-useragent falló ({e}); usando User-Agent de reserva')
        useragent = FALLBACK_USER_AGENT
    runtime_paths.ensure_data_dir()
    with runtime_paths.user_agent_file.open('w') as f:
        f.write(useragent)


def init():
    tprint("scrapyrealestate")
    print(f'scrapyrealestate v{__version__}')

    get_config()
    global registry
    registry = build_default_registry(idealista_proxy=data.proxy_idealista)
    init_logs()
    checks()

    count = 0
    telegram_msg = False
    scrapy_rs_name = data.scrapy_rs_name.replace("-", "_")
    send_first = data.send_first

    while True:
        try:
            runtime_paths.crawl_output(scrapy_rs_name).unlink()
        except FileNotFoundError:
            pass

        # renovamos el User-Agent cada 10 ciclos
        if count % 10 == 0:
            logger.debug('Renovando User-Agent')
            update_useragent()

        # send_first envía ya en el primer ciclo; si no, solo a partir del segundo
        if send_first or count > 0:
            telegram_msg = True

        scrap_realestate(telegram_msg)

        count += 1
        rndtime = random.randint(3, 40) + data.time_update
        logger.info(f"SLEEPING {rndtime} SECONDS")
        time.sleep(rndtime)


if __name__ == "__main__":
    init()
