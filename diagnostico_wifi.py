#!/usr/bin/env python3
"""Diagnóstico de Wi-Fi para Windows, macOS e Linux. Só usa a biblioteca padrão.

Uso:
    python3 diagnostico_wifi.py                # no Windows: python diagnostico_wifi.py
    python3 diagnostico_wifi.py --local sala   # rotula o ponto da casa onde você está

Rode em vários cômodos/andares (--local quarto, --local andar2 ...) e envie os
arquivos relatorio_*.txt gerados. Nada é enviado para fora: o script só faz
ping, abre a página de admin do seu roteador e baixa/sobe dados de teste para
speed.cloudflare.com.
"""
import argparse
import platform
import re
import shutil
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

SO = platform.system()  # Windows, Darwin, Linux
LINHAS = []


def log(txt=""):
    print(txt)
    LINHAS.append(txt)


def run(cmd, timeout=40):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           shell=isinstance(cmd, str), errors="replace")
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return f"erro: {e}"


def gateway():
    if SO == "Windows":
        m = re.search(r"0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)", run("route print -4 0.0.0.0"))
    elif SO == "Darwin":
        m = re.search(r"gateway:\s*(\S+)", run(["route", "-n", "get", "default"]))
    else:
        m = re.search(r"default via (\S+)", run(["ip", "route"]))
    return m.group(1) if m else None


def info_wifi():
    if SO == "Windows":
        return run(["netsh", "wlan", "show", "interfaces"])
    if SO == "Darwin":
        return run("system_profiler SPAirPortDataType", 60)
    if shutil.which("nmcli"):
        return run("nmcli -f IN-USE,SSID,CHAN,FREQ,RATE,SIGNAL,SECURITY dev wifi list")
    return run(["iw", "dev"]) + run("iwconfig 2>/dev/null")


def redes_vizinhas():
    if SO == "Windows":
        return run("netsh wlan show networks mode=bssid", 60)
    if SO == "Darwin":
        return "(macOS: use Diagnóstico Wi-Fi > Janela > Scan, ou 'wdutil info')"
    if shutil.which("nmcli"):
        return run("nmcli -f SSID,CHAN,FREQ,RATE,SIGNAL dev wifi list --rescan yes")
    return "(instale nmcli ou iw para listar redes vizinhas)"


def ping(host, n=30):
    """Retorna (media, jitter, perda%, max) em ms."""
    if SO == "Windows":
        out = run(["ping", "-n", str(n), host], 90)
        tempos = [float(a or b) for a, b in re.findall(r"tempo[=<](\d+)ms|time[=<](\d+)ms", out)]
    else:
        out = run(["ping", "-c", str(n), "-i", "0.2", host], 90)
        tempos = [float(x) for x in re.findall(r"time[=<]([\d.]+)", out)]
    if not tempos:
        return None
    perda = 100 * (n - len(tempos)) / n
    jitter = statistics.mean(abs(a - b) for a, b in zip(tempos, tempos[1:])) if len(tempos) > 1 else 0
    return statistics.mean(tempos), jitter, perda, max(tempos)


def fmt_ping(r):
    if not r:
        return "sem resposta"
    return f"média {r[0]:.1f} ms | jitter {r[1]:.1f} ms | perda {r[2]:.0f}% | máx {r[3]:.0f} ms"


def fabricante_gateway(gw):
    run(["ping", "-c" if SO != "Windows" else "-n", "1", gw])
    saida = run(["arp", "-a", gw] if SO != "Linux" else ["ip", "neigh", "show", gw])
    mac = re.search(r"([0-9a-fA-F]{1,2}[:-]){5}[0-9a-fA-F]{1,2}", saida)
    return mac.group(0) if mac else None


def titulo_admin(gw):
    for esquema in ("http", "https"):
        try:
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(f"{esquema}://{gw}", timeout=4, context=ctx) as r:
                corpo = r.read(20000).decode("utf-8", "replace")
                t = re.search(r"<title>(.*?)</title>", corpo, re.I | re.S)
                srv = r.headers.get("Server", "")
                return f"{(t.group(1).strip() if t else '?')} | Server: {srv}"
        except Exception:  # noqa: BLE001
            continue
    return None


def download(mb=100, segundos=12):
    """Mede download em Mbps com 4 conexões paralelas."""
    import threading
    total = [0]
    fim = time.time() + segundos
    lock = threading.Lock()

    def worker():
        try:
            req = urllib.request.Request(
                f"https://speed.cloudflare.com/__down?bytes={mb * 1_000_000}",
                headers={"User-Agent": "diagnostico-wifi"})
            with urllib.request.urlopen(req, timeout=15) as r:
                while time.time() < fim:
                    b = r.read(65536)
                    if not b:
                        break
                    with lock:
                        total[0] += len(b)
        except Exception:  # noqa: BLE001
            pass

    ini = time.time()
    ts = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    dur = max(time.time() - ini, 0.1)
    return total[0] * 8 / dur / 1e6


def upload(mb=25):
    dados = b"0" * (mb * 1_000_000)
    req = urllib.request.Request("https://speed.cloudflare.com/__up", data=dados,
                                 headers={"User-Agent": "diagnostico-wifi"}, method="POST")
    ini = time.time()
    try:
        urllib.request.urlopen(req, timeout=60).read()
    except Exception:  # noqa: BLE001
        return None
    return mb * 8 / max(time.time() - ini, 0.1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", default="sem-nome", help="onde você está (ex.: sala, quarto-andar2)")
    a = ap.parse_args()

    log(f"=== Diagnóstico Wi-Fi | local: {a.local} | {datetime.now():%Y-%m-%d %H:%M} ===")
    log(f"Sistema: {SO} {platform.release()} | Python {sys.version.split()[0]}")

    gw = gateway()
    log(f"\n[Roteador / gateway] IP: {gw}")
    if gw:
        mac = fabricante_gateway(gw)
        log(f"MAC do roteador: {mac}  (cole em https://maclookup.app para ver o fabricante)")
        log(f"Página de admin: {titulo_admin(gw) or 'não respondeu em http/https'}")

    log("\n[Conexão Wi-Fi atual]")
    log(info_wifi())

    log("\n[Redes vizinhas e canais]")
    log(redes_vizinhas())

    log("\n[Latência] (30 pings cada)")
    if gw:
        log(f"Até o roteador : {fmt_ping(ping(gw))}")
    log(f"Até a internet : {fmt_ping(ping('1.1.1.1'))}")

    log("\n[Velocidade] (aguarde ~30 s)")
    log(f"Download: {download():.0f} Mbps")
    up = upload()
    log(f"Upload  : {f'{up:.0f} Mbps' if up else 'falhou'}")

    arq = f"relatorio_{a.local}.txt"
    with open(arq, "w", encoding="utf-8") as f:
        f.write("\n".join(LINHAS))
    print(f"\nRelatório salvo em {arq}")


if __name__ == "__main__":
    main()
