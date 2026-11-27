#!/usr/bin/env python3
"""Calcula estadisticas AIS (rio + puertos). Extraido de calcular_estadisticas.yml.
Correr desde la raiz del repo historico-ais, despues de scraper_ais.py.

v2 (2026-11-27): la segunda mitad (resumen_diario.json y visitas.json) lee los CSV
fila por fila en vez de cargarlos enteros como listas de dicts. Con ~3,8 M de filas
la v1 llegaba a 1,9 GB y earlyoom la mataba en cada corrida desde el 28-sep-2026;
la v2 usa ~50 MB y produce exactamente la misma salida."""
import json, csv, math, os, glob, sys, urllib.request
from array import array
from calendar import timegm
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

def leer(patron, columnas):
    """Recorre los CSV en orden, fila por fila, sin guardarlos en memoria."""
    for path in sorted(glob.glob(patron)):
        with open(path, encoding="utf-8", newline="") as f:
            r = csv.reader(f)
            cab = next(r, None)
            if not cab:
                continue
            try:
                idx = [cab.index(c) for c in columnas]
            except ValueError:
                print(f"AVISO: {path} sin las columnas esperadas, lo salteo", file=sys.stderr)
                continue
            n = max(idx) + 1
            for row in r:
                if len(row) < n:
                    continue                      # linea truncada
                yield [row[i] for i in idx]

def guardar(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)                         # nunca deja un JSON a medias

# --- censo diario del rio: un set de MMSI por dia ---
por_dia_rio = {}
for fecha, mmsi in leer("data/eventos_rio/*.csv", ["fecha", "mmsi"]):
    s = por_dia_rio.get(fecha)
    if s is None:
        s = por_dia_rio[fecha] = set()
    s.add(mmsi)

guardar("data/resumen_diario.json",
        {"actualizado": ahora.isoformat(),
         "nota": "buques_unicos = MMSI distintos vistos ese dia navegando dentro del poligono del rio.",
         "por_dia": {d: {"rio_buques_unicos": len(m)} for d, m in por_dia_rio.items()}})

# --- visitas a puerto ---
# Por cada (buque, puerto) se guardan solo los instantes (enteros) y el indice del
# nombre: ~12 bytes por evento en vez de un dict de ~450. Se ordena por instante
# igual que la version original, porque los CSV no estan en orden cronologico
# estricto (el de julio 2026 viene desordenado).
TOL = TOLERANCIA_SESION_MIN * 60
nombres, nombre_id = [], {}
ev_ts, ev_nom = {}, {}       # (mmsi, puerto) -> array de epoch / array de indice de nombre
cache_ts = {}
for fecha, hora, puerto, mmsi, nombre in leer("data/eventos_puerto/*.csv",
                                              ["fecha", "hora", "puerto", "mmsi", "nombre"]):
    k = fecha + "T" + hora
    ts = cache_ts.get(k)
    if ts is None:
        try:
            ts = timegm(datetime.fromisoformat(k).timetuple())
        except ValueError:
            continue
        if len(cache_ts) > 5000:
            cache_ts.clear()
        cache_ts[k] = ts
    ni = nombre_id.get(nombre)
    if ni is None:
        ni = nombre_id[nombre] = len(nombres)
        nombres.append(nombre)
    clave = (mmsi, puerto)
    a = ev_ts.get(clave)
    if a is None:
        a = ev_ts[clave] = array("q")
        ev_nom[clave] = array("l")
    a.append(ts)
    ev_nom[clave].append(ni)

def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()

visitas_out = []
def cerrar(clave, ini, fin, ni):
    visitas_out.append({"mmsi": clave[0], "nombre": nombres[ni], "puerto": clave[1],
                        "inicio": iso(ini), "fin": iso(fin),
                        "duracion_horas": round((fin - ini) / 3600, 2)})

for clave, a in ev_ts.items():
    nom = ev_nom[clave]
    orden = range(len(a))
    if any(a[i] > a[i + 1] for i in range(len(a) - 1)):
        orden = sorted(orden, key=a.__getitem__)      # estable, como el sort original
    it = iter(orden)
    i0 = next(it)
    ini = fin = a[i0]; ni = nom[i0]
    for i in it:
        t = a[i]
        if t - fin <= TOL:
            fin = t
        else:
            cerrar(clave, ini, fin, ni)
            ini = fin = t; ni = nom[i]
    cerrar(clave, ini, fin, ni)

guardar("data/visitas.json",
        {"actualizado": ahora.isoformat(),
         "nota": f"Cada visita agrupa corridas consecutivas del mismo buque en el mismo puerto; "
                 f"un hueco <= {TOLERANCIA_SESION_MIN} min no corta la visita, uno mayor cuenta como visita nueva.",
         "visitas": visitas_out})
print(f"OK: {len(visitas_out)} visitas de puerto, {len(por_dia_rio)} dias de censo de rio")
