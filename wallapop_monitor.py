"""
Monitor continuo de chollos Wallapop con alertas Telegram.
Comprueba cada X minutos y te avisa SOLO de chollos nuevos.

Uso:
    pip install requests rich python-dotenv
    1. Crea archivo .env con TELEGRAM_TOKEN y TELEGRAM_CHAT_ID
    2. python wallapop_monitor.py
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
    {"query": "iphone 13",        "precio_min": 200, "precio_max": 700},
    {"query": "iphone 14",        "precio_min": 300, "precio_max": 900},
    {"query": "playstation 5",    "precio_min": 250, "precio_max": 500},
    {"query": "nintendo switch oled", "precio_min": 150, "precio_max": 300},
    {"query": "macbook air m1",   "precio_min": 400, "precio_max": 900},
    {"query": "airpods pro",      "precio_min": 80,  "precio_max": 220},
    {"query": "ipad air",         "precio_min": 200, "precio_max": 600},
    {"query": "apple watch",      "precio_min": 100, "precio_max": 400},
]

DESCUENTO_MIN   = 30            # % minimo para alertar
CAPITAL         = 430           # € disponibles
COMISION_VENTA  = 0.10          # 10%
INTERVALO_MIN   = 15            # minutos entre escaneos

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT   = os.getenv("TELEGRAM_CHAT_ID", "")

# Archivo para recordar chollos ya notificados
HISTORIAL = Path(os.getenv("HISTORIAL_PATH", Path(__file__).parent / "chollos_vistos.json"))
HISTORIAL.parent.mkdir(parents=True, exist_ok=True)

API_URL = "https://api.wallapop.com/api/v3/general/search"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "es-ES,es;q=0.9",
    "DeviceOS": "0",
}


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
        console.print("[yellow]⚠ Telegram no configurado (falta TOKEN/CHAT_ID)[/yellow]")
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
        r = requests.get(API_URL, headers=HEADERS, params={
            "keywords": query,
            "min_sale_price": pmin,
            "max_sale_price": pmax,
            "order_by": "newest",
        }, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("search_objects", []) or data.get("data", {}).get("search_objects", [])
    except Exception as e:
        console.print(f"[red]Error '{query}': {e}[/red]")
        return []


def extraer(item):
    price = item.get("price") or item.get("sale_price", 0)
    if isinstance(price, dict):
        price = price.get("amount", 0)
    return {
        "id":     str(item.get("id", "")),
        "titulo": (item.get("title") or "")[:70],
        "precio": float(price or 0),
        "ciudad": (item.get("user", {}) or {}).get("location", {}).get("city", "")
                  or item.get("location", {}).get("city", ""),
        "url":    f"https://es.wallapop.com/item/{item.get('web_slug', item.get('id',''))}",
    }


def analizar_query(b, historial):
    items = buscar(b["query"], b["precio_min"], b["precio_max"])
    if len(items) < 5:
        return []

    infos = [extraer(i) for i in items if i]
    infos = [i for i in infos if i["precio"] > 0]

    precios = [i["precio"] for i in infos]
    ref = (statistics.median(precios) + statistics.mean(precios)) / 2

    chollos_nuevos = []
    for i in infos:
        if i["precio"] > CAPITAL or i["id"] in historial:
            continue
        desc = (1 - i["precio"] / ref) * 100
        if desc >= DESCUENTO_MIN:
            venta = ref * (1 - COMISION_VENTA)
            beneficio = venta - i["precio"]
            roi = (beneficio / i["precio"]) * 100
            chollos_nuevos.append({
                **i, "descuento": desc, "precio_medio": ref,
                "beneficio": beneficio, "roi": roi, "query": b["query"],
            })
    return chollos_nuevos


def formato_telegram(c):
    return (
        f"🔥 <b>CHOLLO DETECTADO</b>\n"
        f"\n"
        f"📦 <b>{c['titulo']}</b>\n"
        f"💰 Precio: <b>{c['precio']:.0f}€</b> (medio: {c['precio_medio']:.0f}€)\n"
        f"📉 Descuento: <b>-{c['descuento']:.0f}%</b>\n"
        f"📈 Beneficio estimado: <b>+{c['beneficio']:.0f}€</b>\n"
        f"🎯 ROI: <b>{c['roi']:.0f}%</b>\n"
        f"📍 {c['ciudad'] or 'No especificado'}\n"
        f"🏷 Busqueda: {c['query']}\n"
        f"\n"
        f"🔗 <a href=\"{c['url']}\">Ver en Wallapop</a>"
    )


def ciclo(historial):
    console.print(f"\n[dim]{datetime.now().strftime('%H:%M:%S')} - Escaneando {len(BUSQUEDAS)} busquedas...[/dim]")
    total_nuevos = 0
    for b in BUSQUEDAS:
        chollos = analizar_query(b, historial)
        for c in chollos:
            total_nuevos += 1
            historial.add(c["id"])
            console.print(f"[bold green]🔥 NUEVO CHOLLO:[/bold green] {c['titulo']} - {c['precio']:.0f}€ (-{c['descuento']:.0f}%, ROI {c['roi']:.0f}%)")
            enviar_telegram(formato_telegram(c))
            time.sleep(1)  # no saturar Telegram
        time.sleep(2)  # entre queries

    if total_nuevos == 0:
        console.print("[dim]  Sin chollos nuevos este ciclo[/dim]")
    else:
        console.print(f"[bold]✓ {total_nuevos} chollos nuevos enviados a Telegram[/bold]")

    guardar_historial(historial)
    return total_nuevos


def main():
    console.print("[bold cyan]" + "="*70 + "[/bold cyan]")
    console.print(f"[bold cyan] MONITOR WALLAPOP - Alertas Telegram[/bold cyan]")
    console.print("[bold cyan]" + "="*70 + "[/bold cyan]")
    console.print(f"Capital: [yellow]{CAPITAL}€[/yellow] | Descuento min: [yellow]{DESCUENTO_MIN}%[/yellow] | Intervalo: [yellow]{INTERVALO_MIN}min[/yellow]")

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        console.print("\n[bold red]⚠ FALTA CONFIGURAR TELEGRAM[/bold red]")
        console.print("Crea un archivo [cyan].env[/cyan] en esta carpeta con:")
        console.print("  [dim]TELEGRAM_TOKEN=12345:ABCdef...[/dim]")
        console.print("  [dim]TELEGRAM_CHAT_ID=987654321[/dim]")
        console.print("\nVer instrucciones en el README del proyecto.\n")
    else:
        # Test inicial
        if enviar_telegram(f"🤖 Monitor Wallapop activo\nEscaneando cada {INTERVALO_MIN} min"):
            console.print("[green]✓ Telegram conectado[/green]")
        else:
            console.print("[red]✗ Error conectando con Telegram - revisa TOKEN/CHAT_ID[/red]")

    historial = cargar_historial()
    console.print(f"[dim]Historial: {len(historial)} chollos ya vistos[/dim]\n")

    try:
        while True:
            ciclo(historial)
            console.print(f"[dim]💤 Siguiente escaneo en {INTERVALO_MIN} min...[/dim]")
            time.sleep(INTERVALO_MIN * 60)
    except KeyboardInterrupt:
        console.print("\n[yellow]Monitor detenido por el usuario[/yellow]")
        guardar_historial(historial)


if __name__ == "__main__":
    main()
