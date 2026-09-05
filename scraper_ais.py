#!/usr/bin/env python3
"""Descarga y organiza datos AIS. Extraido del workflow actualizar_ais.yml.
Correr desde la raiz del repo historico-ais."""
import json, ssl, urllib.request, datetime, os

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

fuentes = {
    "SanLorenzo": "https://hidrografia.agpse.gob.ar/mapa/AIS_SanLorenzo.json",
    "Rosario":    "https://hidrografia.agpse.gob.ar/mapa/AIS_Rosario.json",
    "DelGuazu":   "https://hidrografia.agpse.gob.ar/mapa/AIS_DelGuazu.json",
    "Braga":      "https://hidrografia.agpse.gob.ar/mapa/AIS_Braga.json",
    "BellaVista": "https://hidrografia.agpse.gob.ar/mapa/AIS_BellaVista.json",
}

os.makedirs("data", exist_ok=True)

datos_anteriores = {}
if os.path.exists("data/latest.json"):
    try:
        with open("data/latest.json", encoding="utf-8") as f:
            datos_anteriores = json.load(f).get("zonas", {})
    except Exception:
        pass

zonas_nuevas = {}
h = {"User-Agent": "Mozilla/5.0 (compatible; AIS-map-updater/1.0)"}
for nombre, url in fuentes.items():
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            zonas_nuevas[nombre] = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"Error bajando {nombre}: {e}")
        zonas_nuevas[nombre] = datos_anteriores.get(nombre, {"error": str(e)})

if zonas_nuevas == datos_anteriores and datos_anteriores:
    print("Sin cambios en las posiciones. No se guarda nada.")
else:
    ahora = datetime.datetime.utcnow()
    ts = ahora.isoformat() + "Z"
    with open("data/latest.json", "w", encoding="utf-8") as f:
        json.dump({"actualizado": ts, "zonas": zonas_nuevas}, f, ensure_ascii=False)
    print("Actualizado: data/latest.json")

    carpeta = os.path.join("data", "historico", ahora.strftime("%Y"), ahora.strftime("%m"))
    os.makedirs(carpeta, exist_ok=True)
    archivo_dia = os.path.join(carpeta, f"{ahora.strftime('%Y-%m-%d')}.json")
    historial = []
    if os.path.exists(archivo_dia):
        try:
            with open(archivo_dia, encoding="utf-8") as f:
                historial = json.load(f)
        except Exception:
            pass
    historial.append({"timestamp": ts, "zonas": zonas_nuevas})
    with open(archivo_dia, "w", encoding="utf-8") as f:
        json.dump(historial, f, ensure_ascii=False)
    print(f"Historial acumulado en: {archivo_dia}")
