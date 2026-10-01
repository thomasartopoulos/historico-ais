#!/usr/bin/env python3
"""Calcula estadisticas AIS (rio + puertos). Extraido de calcular_estadisticas.yml.
Correr desde la raiz del repo historico-ais, despues de scraper_ais.py."""
import json, csv, math, os, glob, urllib.request
from datetime import datetime, timezone
from shapely.geometry import shape, Point
from shapely.ops import unary_union
from shapely.prepared import prep

ESCALA = 600000
RADIO_TIERRA_M = 6371000
DIST_MAX_REGISTRO = 500
TOLERANCIA_SESION_MIN = 30
BASE_MAPA = "https://raw.githubusercontent.com/thomasartopoulos/ais-parana-map/main/data"

def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1); dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlmb/2)**2
    return 2 * RADIO_TIERRA_M * math.asin(math.sqrt(a))

def bajar_json(nombre):
    with urllib.request.urlopen(f"{BASE_MAPA}/{nombre}", timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))

puertos_gj = bajar_json("puertos.geojson")
puertos = [(f["properties"]["nombre"], f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0])
           for f in puertos_gj["features"]]
rios_gj = bajar_json("rios.geojson")
rios_prep = prep(unary_union([shape(f["geometry"]) for f in rios_gj["features"] if f["geometry"]]))

with open("data/latest.json", encoding="utf-8") as f:
    combinado = json.load(f)

ahora = datetime.now(timezone.utc)
fecha = ahora.strftime("%Y-%m-%d"); hora = ahora.strftime("%H:%M:%S"); mes = ahora.strftime("%Y-%m")
eventos_rio, eventos_puerto = [], []

for zona, dz in (combinado.get("zonas") or {}).items():
    for mmsi, tgt in (dz.get("tgts") or {}).items():
        if tgt.get("t") not in (0, 1):
            continue
        x, y = tgt.get("x"), tgt.get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            continue
        lat, lon = y / ESCALA, x / ESCALA
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        nombre = tgt.get("n", ""); punto = Point(lon, lat)
        if rios_prep.contains(punto):
            eventos_rio.append({"fecha": fecha, "hora": hora, "mmsi": mmsi, "nombre": nombre, "zona": zona})
        if tgt.get("s") == 0:
            mejor = None
            for np_, plat, plon in puertos:
                d = haversine_m(lat, lon, plat, plon)
                if d <= DIST_MAX_REGISTRO and (mejor is None or d < mejor[1]):
                    mejor = (np_, d)
            if mejor:
                eventos_puerto.append({"fecha": fecha, "hora": hora, "puerto": mejor[0], "mmsi": mmsi,
                                       "nombre": nombre, "distancia_m": round(mejor[1], 1), "zona": zona})

def append_csv(path, filas, campos):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    nuevo = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        if nuevo: w.writeheader()
        w.writerows(filas)

append_csv(f"data/eventos_rio/{mes}.csv", eventos_rio, ["fecha","hora","mmsi","nombre","zona"])
append_csv(f"data/eventos_puerto/{mes}.csv", eventos_puerto, ["fecha","hora","puerto","mmsi","nombre","distancia_m","zona"])
print(f"Rio: {len(eventos_rio)} | Puerto (<=500m, fondeado): {len(eventos_puerto)}")

def cargar_todos(patron):
    filas = []
    for path in sorted(glob.glob(patron)):
        with open(path, encoding="utf-8") as f:
            filas.extend(csv.DictReader(f))
    for row in filas:
        row["ts"] = datetime.fromisoformat(f"{row['fecha']}T{row['hora']}").replace(tzinfo=timezone.utc)
    return filas

por_dia_rio = {}
for row in cargar_todos("data/eventos_rio/*.csv"):
    por_dia_rio.setdefault(row["fecha"], set()).add(row["mmsi"])

with open("data/resumen_diario.json", "w", encoding="utf-8") as f:
    json.dump({"actualizado": ahora.isoformat(),
               "nota": "buques_unicos = MMSI distintos vistos ese dia navegando dentro del poligono del rio.",
               "por_dia": {d: {"rio_buques_unicos": len(m)} for d, m in por_dia_rio.items()}},
              f, ensure_ascii=False)

eventos_p = cargar_todos("data/eventos_puerto/*.csv")
grupos = {}
for row in eventos_p:
    grupos.setdefault((row["mmsi"], row["puerto"]), []).append(row)

visitas = []
for (mmsi, puerto), evs in grupos.items():
    evs.sort(key=lambda r: r["ts"])
    sesion = [evs[0]]
    for ev in evs[1:]:
        gap = (ev["ts"] - sesion[-1]["ts"]).total_seconds() / 60
        if gap <= TOLERANCIA_SESION_MIN:
            sesion.append(ev)
        else:
            visitas.append(sesion); sesion = [ev]
    visitas.append(sesion)

visitas_out = []
for s in visitas:
    inicio, fin = s[0]["ts"], s[-1]["ts"]
    visitas_out.append({"mmsi": s[0]["mmsi"], "nombre": s[0]["nombre"], "puerto": s[0]["puerto"],
                        "inicio": inicio.isoformat(), "fin": fin.isoformat(),
                        "duracion_horas": round((fin - inicio).total_seconds() / 3600, 2)})

with open("data/visitas.json", "w", encoding="utf-8") as f:
    json.dump({"actualizado": ahora.isoformat(),
               "nota": f"Cada visita agrupa corridas consecutivas del mismo buque en el mismo puerto; "
                       f"un hueco <= {TOLERANCIA_SESION_MIN} min no corta la visita, uno mayor cuenta como visita nueva.",
               "visitas": visitas_out}, f, ensure_ascii=False)
print(f"OK: {len(visitas_out)} visitas de puerto, {len(por_dia_rio)} dias de censo de rio")
