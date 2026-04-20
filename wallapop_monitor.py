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
    # precio_ref = precio medio realista de reventa en segunda mano (Wallapop/Vinted/eBay)
    # NO es precio de tienda nueva — es lo que TU puedes pedir al revender
    {"query": "iphone 13",            "precio_min": 200, "precio_max": 650, "precio_ref": 420},
    {"query": "iphone 14",            "precio_min": 280, "precio_max": 800, "precio_ref": 530},
    {"query": "playstation 5",        "precio_min": 250, "precio_max": 480, "precio_ref": 380},
    {"query": "nintendo switch oled", "precio_min": 150, "precio_max": 290, "precio_ref": 240},
    {"query": "macbook air m1",       "precio_min": 400, "precio_max": 850, "precio_ref": 680},
    {"query": "airpods pro",          "precio_min": 80,  "precio_max": 200, "precio_ref": 150},
    {"query": "ipad air",             "precio_min": 200, "precio_max": 580, "precio_ref": 400},
    {"query": "apple watch",          "precio_min": 100, "precio_max": 380, "precio_ref": 250},
    {"query": "gopro",                "precio_min": 80,  "precio_max": 300, "precio_ref": 180},
    {"query": "dyson",                "precio_min": 100, "precio_max": 400, "precio_ref": 250},
]

DESCUENTO_MIN  = 30       # % minimo sobre precio medio
CAPITAL        = 430      # € disponibles
COMISION_VENTA = 0.10     # 10% (envio + comision plataforma)
INTERVALO_MIN  = 15       # minutos entre escaneos

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
            "per_page": 96,
            "order": "newest_first",
            "currency": "EUR",
            "country_ids[]": 197,   # 197 = España
        }, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("items", [])
    except Exception as e:
        console.print(f"[red]Error '{query}': {e}[/red]")
        return []


def extraer(item):
    try:
        precio = float(item.get("price", {}).get("amount", 0) or item.get("price", 0) or 0)
    except Exception:
        precio = 0
    return {
        "id":     str(item.get("id", "")),
        "titulo": (item.get("title") or "")[:70],
        "precio": precio,
        "marca":  (item.get("brand_title") or ""),
        "talla":  (item.get("size_title") or ""),
        "ciudad": (item.get("user", {}) or {}).get("city", ""),
        "url":    item.get("url") or f"https://www.vinted.es/items/{item.get('id','')}",
        "foto":   (item.get("photos") or [{}])[0].get("url", "") if item.get("photos") else "",
    }


def analizar_query(b, historial):
    items = buscar(b["query"], b["precio_min"], b["precio_max"])
    if len(items) < 5:
        return []

    infos = [extraer(i) for i in items if i]
    infos = [i for i in infos if i["precio"] > 0]

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
    total_nuevos = 0
    for b in BUSQUEDAS:
        chollos = analizar_query(b, historial)
        for c in chollos:
            total_nuevos += 1
            historial.add(c["id"])
            console.print(
                f"[bold green]🔥 CHOLLO:[/bold green] {c['titulo']} - "
                f"{c['precio']:.0f}€ (-{c['descuento']:.0f}%, ROI {c['roi']:.0f}%)"
            )
            enviar_telegram(formato_telegram(c))
            time.sleep(1)
        time.sleep(3)  # pausa entre queries para no saturar

    if total_nuevos == 0:
        console.print("[dim]  Sin chollos nuevos este ciclo[/dim]")
    else:
        console.print(f"[bold green]✓ {total_nuevos} chollos enviados a Telegram[/bold green]")

    guardar_historial(historial)
    return total_nuevos


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
