"""
Monitor continuo de chollos en VINTED con alertas Telegram.
Vinted permite peticiones desde servidor a diferencia de Wallapop.
Comprueba cada X minutos y avisa SOLO de chollos nuevos por Telegram.

Uso:
    pip install requests rich python-dotenv
    python wallapop_monitor.py
"""

import os
import json
import time
import requests
import statistics
from datetime import datetime
from pathlib import Path
from rich.console import Console

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

console = Console()

# ===== CONFIGURACION =====
BUSQUEDAS = [
    # precio_ref = precio REAL de reventa en Vinted/Wallapop España (verificado)
    # Si alguien vende por debajo de esto con descuento >= DESCUENTO_MIN es chollo
    {"query": "iphone 13",            "precio_min": 150, "precio_max": 350, "precio_ref": 300},
    {"query": "iphone 14",            "precio_min": 200, "precio_max": 450, "precio_ref": 380},
    {"query": "playstation 5",        "precio_min": 200, "precio_max": 420, "precio_ref": 350},
    {"query": "nintendo switch oled", "precio_min": 120, "precio_max": 260, "precio_ref": 210},
    {"query": "macbook air m1",       "precio_min": 350, "precio_max": 750, "precio_ref": 580},
    {"query": "airpods pro",          "precio_min": 60,  "precio_max": 170, "precio_ref": 130},
    {"query": "ipad air",             "precio_min": 150, "precio_max": 450, "precio_ref": 320},
    {"query": "apple watch series 7", "precio_min": 100, "precio_max": 300, "precio_ref": 200},
    {"query": "gopro hero 10",        "precio_min": 80,  "precio_max": 250, "precio_ref": 160},
    {"query": "dyson v11",            "precio_min": 120, "precio_max": 350, "precio_ref": 230},
]

DESCUENTO_MIN      = 35       # % minimo de descuento vs precio ref
BENEFICIO_MIN      = 60       # € minimo de beneficio real
CAPITAL            = 430      # € disponibles
COMISION_VENTA     = 0.10     # 10% (envio + comision plataforma)
INTERVALO_MIN      = 15       # minutos entre escaneos
MAX_ALERTAS_CICLO  = 3        # maximo de alertas por escaneo (evita spam)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")

HISTORIAL = Path(os.getenv("HISTORIAL_PATH", Path(__file__).parent / "chollos_vistos.json"))
HISTORIAL.parent.mkdir(parents=True, exist_ok=True)

# ===== VINTED API =====
VINTED_URL = "https://www.vinted.es/api/v2/catalog/items"
VINTED_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://www.vinted.es/",
    "Origin": "https://www.vinted.es",
}

_sesion = None

def get_sesion():
    global _sesion
    if _sesion is None:
        _sesion = requests.Session()
        _sesion.headers.update(VINTED_HEADERS)
        try:
            # Obtener cookies visitando la web primero
            _sesion.get("https://www.vinted.es/", timeout=15)
            time.sleep(2)
        except Exception:
            pass
    return _sesion


def cargar_historial():
    if HISTORIAL.exists():
        try:
            return set(json.loads(HISTORIAL.read_text()))
        except Exception:
            return set()
    return set()


def guardar_historial(ids):
    HISTORIAL.write_text(json.dumps(list(ids)))


def enviar_telegram(mensaje):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT,
            "text": mensaje,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }, timeout=10)
        return r.status_code == 200
    except Exception as e:
        console.print(f"[red]Error Telegram: {e}[/red]")
        return False


def buscar(query, pmin, pmax):
    try:
        s = get_sesion()
        r = s.get(VINTED_URL, params={
            "search_text": query,
            "price_from": pmin,
            "price_to": pmax,
            "per_page": 60,
            "order": "newest_first",
            "currency": "EUR",
        }, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("items", [])
    except Exception as e:
        console.print(f"[red]Error '{query}': {e}[/red]")
        return []


# Palabras que delatan que el anuncio es de otro pais
PALABRAS_EXTRANJERAS = [
    "bleu","vert","blanc","noir","rouge","tres","bon","etat","boite","cable",
    "fonctionne","parfaitement","negociable","lumiere","go ","ricondizionato",
    "bianco","nero","ottime","condizioni","videogiochi","scelle","lire",
]

def es_espanol(titulo):
    t = titulo.lower()
    return not any(p in t for p in PALABRAS_EXTRANJERAS)


def extraer(item):
    try:
        precio = float(item.get("price", {}).get("amount", 0) or item.get("price", 0) or 0)
    except Exception:
        precio = 0
    titulo = (item.get("title") or "")[:70]
    if not es_espanol(titulo):
        return None  # descartar anuncios extranjeros
    return {
        "id":     str(item.get("id", "")),
        "titulo": titulo,
        "precio": precio,
        "marca":  (item.get("brand_title") or ""),
        "talla":  (item.get("size_title") or ""),
        "ciudad": (item.get("user", {}) or {}).get("city", ""),
        "url":    item.get("url") or f"https://www.vinted.es/items/{item.get('id','')}",
    }


def analizar_query(b, historial):
    items = buscar(b["query"], b["precio_min"], b["precio_max"])
    if len(items) < 5:
        return []

    infos = [extraer(i) for i in items if i]
    infos = [i for i in infos if i is not None and i["precio"] > 0]

    # Precio de referencia: usamos el fijo si existe, si no calculamos media de Vinted
    if b.get("precio_ref"):
        ref = b["precio_ref"]
        ref_tipo = "mercado 2a mano"
    else:
        precios = [i["precio"] for i in infos]
        ref = (statistics.median(precios) + statistics.mean(precios)) / 2
        ref_tipo = "media Vinted"

    chollos = []
    for i in infos:
        if i["precio"] > CAPITAL or i["id"] in historial:
            continue
        desc = (1 - i["precio"] / ref) * 100
        if desc >= DESCUENTO_MIN:
            venta = ref * (1 - COMISION_VENTA)
            beneficio = venta - i["precio"]
            roi = (beneficio / i["precio"]) * 100
            if beneficio < BENEFICIO_MIN:  # filtrar chollos con poco margen real
                continue
            chollos.append({
                **i,
                "descuento": desc,
                "precio_medio": ref,
                "ref_tipo": ref_tipo,
                "beneficio": beneficio,
                "roi": roi,
                "query": b["query"],
            })
    return chollos


def formato_telegram(c):
    marca = f" | {c['marca']}" if c["marca"] else ""
    talla = f" | Talla: {c['talla']}" if c["talla"] else ""
    return (
        f"🔥 <b>CHOLLO EN VINTED</b>\n"
        f"\n"
        f"📦 <b>{c['titulo']}</b>{marca}{talla}\n"
        f"💰 Precio: <b>{c['precio']:.0f}€</b>\n"
        f"🏷 Ref. reventa ({c['ref_tipo']}): {c['precio_medio']:.0f}€\n"
        f"📉 Descuento vs reventa: <b>-{c['descuento']:.0f}%</b>\n"
        f"📈 Beneficio estimado: <b>+{c['beneficio']:.0f}€</b>\n"
        f"🎯 ROI: <b>{c['roi']:.0f}%</b>\n"
        f"📍 {c['ciudad'] or 'No especificado'}\n"
        f"🏷 Busqueda: {c['query']}\n"
        f"\n"
        f"🔗 <a href=\"{c['url']}\">Ver en Vinted</a>"
    )


def ciclo(historial):
    console.print(f"\n[dim]{datetime.now().strftime('%H:%M:%S')} - Escaneando {len(BUSQUEDAS)} busquedas...[/dim]")

    # 1. Recoger todos los chollos de todas las busquedas
    todos = []
    for b in BUSQUEDAS:
        chollos = analizar_query(b, historial)
        todos.extend(chollos)
        time.sleep(3)

    # 2. Ordenar por ROI y quedarse con los mejores
    todos.sort(key=lambda x: x["roi"], reverse=True)
    mejores = todos[:MAX_ALERTAS_CICLO]

    # 3. Marcar todos como vistos (aunque no se envien, para no repetir)
    for c in todos:
        historial.add(c["id"])

    # 4. Enviar solo los mejores
    for c in mejores:
        console.print(
            f"[bold green]🔥 CHOLLO:[/bold green] {c['titulo']} - "
            f"{c['precio']:.0f}€ (-{c['descuento']:.0f}%, ROI {c['roi']:.0f}%)"
        )
        enviar_telegram(formato_telegram(c))
        time.sleep(1)

    if not mejores:
        console.print("[dim]  Sin chollos nuevos este ciclo[/dim]")
    else:
        console.print(f"[bold green]✓ {len(mejores)} mejores chollos enviados a Telegram[/bold green]")

    guardar_historial(historial)
    return len(mejores)


def main():
    console.print("[bold cyan]" + "="*70 + "[/bold cyan]")
    console.print(f"[bold cyan] MONITOR VINTED - Alertas Telegram[/bold cyan]")
    console.print("[bold cyan]" + "="*70 + "[/bold cyan]")
    console.print(
        f"Capital: [yellow]{CAPITAL}€[/yellow] | "
        f"Descuento min: [yellow]{DESCUENTO_MIN}%[/yellow] | "
        f"Intervalo: [yellow]{INTERVALO_MIN}min[/yellow]"
    )

    if enviar_telegram(f"🤖 Monitor Vinted activo\nEscaneando cada {INTERVALO_MIN} min"):
        console.print("[green]✓ Telegram conectado[/green]")
    else:
        console.print("[red]✗ Error Telegram - revisa TOKEN/CHAT_ID[/red]")

    historial = cargar_historial()
    console.print(f"[dim]Historial: {len(historial)} chollos ya vistos[/dim]\n")

    try:
        while True:
            ciclo(historial)
            console.print(f"[dim]💤 Siguiente escaneo en {INTERVALO_MIN} min...[/dim]")
            time.sleep(INTERVALO_MIN * 60)
    except KeyboardInterrupt:
        console.print("\n[yellow]Monitor detenido[/yellow]")
        guardar_historial(historial)


if __name__ == "__main__":
    main()
