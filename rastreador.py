#!/usr/bin/env python3
"""Rastreador de ofertas y precios inusuales.

Uso:
    python rastreador.py correr        # recorre las tiendas, guarda precios y avisa
    python rastreador.py diagnostico   # prueba cada tienda y dice que lector funciona

Solo usa la biblioteca estandar de Python (no hay que instalar nada).
"""
import argparse
import gzip
import html
import json
import os
import re
import sqlite3
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

UA = ("Mozilla/5.0 (Linux; Android 14; SM-S938B) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36")
PAUSA = 1.0  # segundos entre pedidos a una misma tienda


# ---------------------------------------------------------------- utilidades

def descargar(url, intentos=2, espera=25):
    """Baja una URL y devuelve el texto. Lanza excepcion si falla."""
    ultimo = None
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "es-AR,es;q=0.9",
                "Accept-Encoding": "gzip",
            })
            with urllib.request.urlopen(req, timeout=espera) as r:
                crudo = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    crudo = gzip.decompress(crudo)
                return crudo.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            ultimo = e
            time.sleep(2 * (i + 1))
    raise ultimo


def numero(v, _prof=0):
    """Convierte casi cualquier cosa en un precio (float) o None."""
    if v is None or isinstance(v, bool) or _prof > 4:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    if isinstance(v, str):
        s = re.sub(r"[^\d.,]", "", v)
        if not s or not re.search(r"\d", s):
            return None
        if "." in s and "," in s:
            if s.rfind(",") > s.rfind("."):      # 1.749.999,50
                s = s.replace(".", "").replace(",", ".")
            else:                                 # 1,749,999.50
                s = s.replace(",", "")
        elif "," in s:
            partes = s.split(",")
            if len(partes) == 2 and len(partes[1]) <= 2:
                s = s.replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "." in s and re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")                # 1.749.999
        try:
            n = float(s)
        except ValueError:
            return None
        return n if n > 0 else None
    if isinstance(v, dict):
        for k in ("min", "amount", "value", "price", "Price"):
            if k in v:
                n = numero(v[k], _prof + 1)
                if n:
                    return n
        for x in v.values():
            n = numero(x, _prof + 1)
            if n:
                return n
        return None
    if isinstance(v, list):
        for x in v:
            n = numero(x, _prof + 1)
            if n:
                return n
    return None


def pesos(n):
    return "$" + f"{round(n):,}".replace(",", ".")


_UNIDADES = re.compile(
    r"^\d+([.,]\d+)?(gb|tb|mb|hz|khz|w|kw|v|mah|ah|btu|kg|g|cm|mm|m|l|lt|lts|"
    r"fps|mp|rpm|k|p|hd|x\d+|fg|frig|kcal)$", re.I)


def clave_modelo(titulo, marca="", ean=""):
    """Clave para reconocer el mismo producto en distintas tiendas."""
    if ean and re.fullmatch(r"\d{8,14}", str(ean)):
        return "EAN:" + str(ean)
    candidatos = []
    for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-/.]{3,}", titulo or ""):
        limpio = re.sub(r"[^A-Za-z0-9]", "", t).upper()
        letras = sum(c.isalpha() for c in limpio)
        digitos = sum(c.isdigit() for c in limpio)
        if len(limpio) >= 5 and letras >= 2 and digitos >= 2 and not _UNIDADES.match(t):
            candidatos.append(limpio)
    if not candidatos:
        return ""
    modelo = max(candidatos, key=len)
    m = re.sub(r"[^A-Za-z0-9]", "", marca or "").upper()
    if m:
        return m + ":" + modelo
    return modelo if len(modelo) >= 7 else ""


def es_accesorio(titulo, excluir):
    t = (titulo or "").lower()
    return any(re.search(r"\b" + re.escape(p.lower()) + r"\b", t) for p in excluir)


# ------------------------------------------------------------------ lectores

def _producto(tienda, sku, titulo, precio, lista=None, url="", marca="", ean="", vendedor=""):
    div = float(tienda.get("divisor", 1) or 1)
    precio = precio / div if precio else None
    lista = lista / div if lista else None
    if lista and precio and lista <= precio:
        lista = None
    return {"tienda": tienda["nombre"], "sku": str(sku), "titulo": (titulo or "").strip(),
            "precio": precio, "lista": lista, "url": url, "marca": (marca or "").strip(),
            "ean": str(ean or ""), "vendedor": vendedor or ""}


def interpretar_vtex(tienda, datos):
    """Convierte la respuesta de catalogo de VTEX en productos."""
    salida = []
    if not isinstance(datos, list):
        return salida
    for prod in datos:
        if not isinstance(prod, dict):
            continue
        for item in prod.get("items") or []:
            mejor = None
            for s in item.get("sellers") or []:
                of = s.get("commertialOffer") or {}
                precio = numero(of.get("Price"))
                if not precio or (of.get("AvailableQuantity") or 0) <= 0:
                    continue
                if mejor is None or precio < mejor[0]:
                    mejor = (precio, numero(of.get("ListPrice")), s.get("sellerName", ""))
            if not mejor:
                continue
            salida.append(_producto(
                tienda, item.get("itemId") or prod.get("productId"),
                item.get("nameComplete") or prod.get("productName"),
                mejor[0], mejor[1], prod.get("link", ""), prod.get("brand", ""),
                item.get("ean", ""), mejor[2]))
    return salida


def leer_vtex(tienda, termino, max_paginas):
    base = tienda["url"].rstrip("/")
    salida = []
    for pag in range(max_paginas):
        desde = pag * 50
        url = (f"{base}/api/catalog_system/pub/products/search"
               f"?ft={urllib.parse.quote(termino)}&_from={desde}&_to={desde + 49}")
        datos = json.loads(descargar(url))
        nuevos = interpretar_vtex(tienda, datos)
        salida.extend(nuevos)
        if not isinstance(datos, list) or len(datos) < 50:
            break
        time.sleep(PAUSA)
    return salida


_CLAVES_NOMBRE = ("title", "name", "productName", "displayName", "nombre")
_CLAVES_PRECIO = ("salePrice", "salePrices", "sellingPrice", "finalPrice", "bestPrice",
                  "price", "Price", "precio")
_CLAVES_LISTA = ("listPrice", "listPrices", "ListPrice", "regularPrice", "originalPrice",
                 "oldPrice", "priceWithoutDiscount", "precioLista")
_CLAVES_ID = ("sku", "skuId", "id", "productId", "itemId", "productID", "slug", "code")
_CLAVES_URL = ("url", "link", "href", "slug", "permalink")


def _como_producto(d, tienda):
    tipo = d.get("@type")
    es_ld = tipo == "Product" or (isinstance(tipo, list) and "Product" in tipo)
    nombre = next((d[k] for k in _CLAVES_NOMBRE if isinstance(d.get(k), str) and d[k].strip()), None)
    if not nombre:
        return None
    marca = d.get("brand")
    if isinstance(marca, dict):
        marca = marca.get("name", "")
    if not isinstance(marca, str):
        marca = ""
    base = tienda["url"].rstrip("/") + "/"
    if es_ld:
        of = d.get("offers") or {}
        if isinstance(of, list):
            of = of[0] if of else {}
        precio = numero(of.get("price")) or numero(of.get("lowPrice"))
        sku = d.get("sku") or d.get("productID") or d.get("mpn") or d.get("url") or nombre
        url = d.get("url") or of.get("url") or ""
        if not precio:
            return None
        return _producto(tienda, sku, nombre, precio, None,
                         urllib.parse.urljoin(base, url) if url else "", marca,
                         d.get("gtin13") or d.get("gtin") or "")
    precio = next((n for n in (numero(d.get(k)) for k in _CLAVES_PRECIO) if n), None)
    sku = next((d[k] for k in _CLAVES_ID if isinstance(d.get(k), (str, int)) and str(d[k])), None)
    if not precio or sku is None:
        return None
    lista = next((n for n in (numero(d.get(k)) for k in _CLAVES_LISTA) if n), None)
    url = next((d[k] for k in _CLAVES_URL if isinstance(d.get(k), str) and d[k]), "")
    tiene_lista = any(k in d for k in _CLAVES_LISTA)
    if not (url or tiene_lista or marca):
        return None  # probablemente no es un producto (envio, cuota, etc.)
    if url and not url.startswith("http"):
        if "/" not in url.strip("/"):
            url = tienda.get("prefijo_producto", "/") + url.strip("/") + "/"
        url = urllib.parse.urljoin(base, url)
    vendedor = d.get("seller") or d.get("vendor") or ""
    if isinstance(vendedor, dict):
        vendedor = vendedor.get("name") or vendedor.get("commercialName") or ""
    return _producto(tienda, sku, nombre, precio, lista, url, marca,
                     d.get("ean") or d.get("gtin") or "", vendedor if isinstance(vendedor, str) else "")


def _recorrer(obj, tienda, salida, prof=0):
    if prof > 40:
        return
    if isinstance(obj, dict):
        p = _como_producto(obj, tienda)
        if p:
            salida.append(p)
            return
        for v in obj.values():
            _recorrer(v, tienda, salida, prof + 1)
    elif isinstance(obj, list):
        for v in obj:
            _recorrer(v, tienda, salida, prof + 1)


def extraer_productos_html(texto, tienda):
    """Busca productos en los bloques JSON incrustados en una pagina."""
    salida = []
    for m in re.finditer(r"<script([^>]*)>(.*?)</script>", texto, re.S | re.I):
        attrs, cuerpo = m.group(1).lower(), m.group(2).strip()
        if not cuerpo or cuerpo[0] not in "{[":
            continue
        if "json" not in attrs and "__next_data__" not in attrs:
            continue
        try:
            datos = json.loads(html.unescape(cuerpo) if "&quot;" in cuerpo else cuerpo)
        except ValueError:
            continue
        _recorrer(datos, tienda, salida)
    vistos, unicos = set(), []
    for p in salida:
        if p["sku"] not in vistos:
            vistos.add(p["sku"])
            unicos.append(p)
    return unicos


def leer_html(tienda, termino, max_paginas):
    plantilla = tienda.get("busqueda")
    if not plantilla:
        return []
    salida, vistos = [], set()
    for pag in range(1, max_paginas + 1):
        url = plantilla.format(q=urllib.parse.quote(termino), p=pag)
        nuevos = [p for p in extraer_productos_html(descargar(url), tienda) if p["sku"] not in vistos]
        if not nuevos:
            break
        vistos.update(p["sku"] for p in nuevos)
        salida.extend(nuevos)
        time.sleep(PAUSA)
    return salida


def leer(tienda, termino, max_paginas):
    """Devuelve (productos, lector_usado)."""
    modo = tienda.get("lector", "auto")
    if modo in ("vtex", "auto"):
        try:
            prods = leer_vtex(tienda, termino, max_paginas)
            if prods or modo == "vtex":
                return prods, "vtex"
        except Exception:  # noqa: BLE001
            if modo == "vtex":
                raise
    return leer_html(tienda, termino, max_paginas), "html"


# ------------------------------------------------------------- base de datos

def abrir(ruta):
    os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
    c = sqlite3.connect(ruta)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS productos(
            tienda TEXT, sku TEXT, titulo TEXT, marca TEXT, clave TEXT, url TEXT,
            categoria TEXT, vendedor TEXT, primera_vez TEXT, ultima_vez TEXT,
            ultimo_precio REAL, ultima_lista REAL, PRIMARY KEY(tienda, sku));
        CREATE TABLE IF NOT EXISTS precios(
            tienda TEXT, sku TEXT, fecha TEXT, precio REAL, lista REAL);
        CREATE INDEX IF NOT EXISTS i_precios ON precios(tienda, sku, fecha);
        CREATE INDEX IF NOT EXISTS i_clave ON productos(clave);
        CREATE TABLE IF NOT EXISTS alertas(
            tienda TEXT, sku TEXT, tipo TEXT, precio REAL, fecha TEXT,
            PRIMARY KEY(tienda, sku, tipo, precio));
    """)
    return c


def _fecha(s):
    return datetime.fromisoformat(s)


def historial(c, tienda, sku, ahora, dias):
    """Tramos (precio, lista, dias_vigente) dentro de la ventana, antes de la corrida actual."""
    fila = c.execute("SELECT ultima_vez FROM productos WHERE tienda=? AND sku=?", (tienda, sku)).fetchone()
    if not fila:
        return []
    fin_total = min(ahora, _fecha(fila[0]))
    desde = ahora - timedelta(days=dias)
    filas = c.execute("SELECT fecha, precio, lista FROM precios WHERE tienda=? AND sku=? ORDER BY fecha",
                      (tienda, sku)).fetchall()
    tramos = []
    for i, (f, p, l) in enumerate(filas):
        ini = max(_fecha(f), desde)
        fin = _fecha(filas[i + 1][0]) if i + 1 < len(filas) else fin_total
        if fin > ini:
            tramos.append((p, l, (fin - ini).total_seconds() / 86400))
    return tramos


def mediana_ponderada(pares):
    """pares: [(valor, peso)]"""
    pares = sorted(pares)
    total = sum(w for _, w in pares)
    acum = 0.0
    for v, w in pares:
        acum += w
        if acum >= total / 2:
            return v
    return pares[-1][0]


def guardar(c, p, categoria, ahora, dias_hist):
    """Registra una observacion. Devuelve None si no hubo cambio, o el historial previo."""
    t, sku, iso = p["tienda"], p["sku"], ahora.isoformat()
    clave = clave_modelo(p["titulo"], p["marca"], p["ean"])
    fila = c.execute("SELECT ultimo_precio, ultima_lista FROM productos WHERE tienda=? AND sku=?",
                     (t, sku)).fetchone()
    if fila is None:
        c.execute("INSERT INTO productos VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (t, sku, p["titulo"], p["marca"], clave, p["url"], categoria, p["vendedor"],
                   iso, iso, p["precio"], p["lista"]))
        c.execute("INSERT INTO precios VALUES(?,?,?,?,?)", (t, sku, iso, p["precio"], p["lista"]))
        return []
    hist = None
    cambio = abs((fila[0] or 0) - p["precio"]) > 0.5 or abs((fila[1] or 0) - (p["lista"] or 0)) > 0.5
    if cambio:
        hist = historial(c, t, sku, ahora, dias_hist)
        c.execute("INSERT INTO precios VALUES(?,?,?,?,?)", (t, sku, iso, p["precio"], p["lista"]))
    c.execute("""UPDATE productos SET titulo=?, url=?, clave=?, vendedor=?, ultima_vez=?,
                 ultimo_precio=?, ultima_lista=? WHERE tienda=? AND sku=?""",
              (p["titulo"], p["url"], clave, p["vendedor"], iso, p["precio"], p["lista"], t, sku))
    return hist


def precios_otras_tiendas(c, clave, tienda, ahora, dias=3):
    if not clave:
        return []
    desde = (ahora - timedelta(days=dias)).isoformat()
    return [r[0] for r in c.execute(
        "SELECT MIN(ultimo_precio) FROM productos WHERE clave=? AND tienda!=? AND ultima_vez>=? "
        "GROUP BY tienda", (clave, tienda, desde)) if r[0]]


# ----------------------------------------------------------------- deteccion

REGLAS = {
    "dias_historial": 60,       # ventana de historia que se mira
    "caida_bug": 0.45,          # cae 45% o mas contra su precio habitual -> posible bug
    "min_dias_caida": 2,        # dias de historia necesarios para esa regla
    "descuento_extremo": 0.70,  # 70% o mas contra el precio de lista publicado
    "minimo_historico": 0.10,   # 10% por debajo del minimo anterior -> oferta real
    "min_dias_minimo": 7,
    "inflado_descuento": 0.20,  # dice 20% off o mas...
    "inflado_margen": 0.15,     # ...pero el "antes" supera en 15% al maximo que realmente cobro
    "otras_tiendas": 0.30,      # 30% mas barato que la mediana de otras tiendas
    "min_otras_tiendas": 2,
    "max_alertas_por_corrida": 15,
}


def evaluar(precio, lista, hist, otras, reglas=None, piso=None):
    """Devuelve una lista de (tipo, detalle), de mas a menos grave.

    hist: [(precio, lista, dias)] previo a esta observacion. otras: precios en otras tiendas.
    """
    r = dict(REGLAS)
    r.update(reglas or {})
    al = []
    dias = sum(d for _, _, d in hist)
    med = mediana_ponderada([(p, d) for p, _, d in hist]) if hist else None

    if piso and precio < piso:
        al.append(("PRECIO ABSURDO", f"por debajo del piso de la categoria ({pesos(piso)})"))
    if med and dias >= r["min_dias_caida"] and precio <= med * (1 - r["caida_bug"]):
        al.append(("POSIBLE BUG", f"cayo {round((1 - precio / med) * 100)}% contra su precio habitual ({pesos(med)})"))
    if lista and precio <= lista * (1 - r["descuento_extremo"]):
        al.append(("DESCUENTO EXTREMO", f"{round((1 - precio / lista) * 100)}% contra el precio de lista ({pesos(lista)})"))
    if len(otras) >= r["min_otras_tiendas"]:
        m_otras = statistics.median(otras)
        if precio <= m_otras * (1 - r["otras_tiendas"]):
            al.append(("MAS BARATO QUE OTRAS TIENDAS",
                       f"{round((1 - precio / m_otras) * 100)}% menos que la mediana de {len(otras)} tiendas ({pesos(m_otras)})"))
    if hist and dias >= r["min_dias_minimo"]:
        minimo = min(p for p, _, _ in hist)
        maximo = max(p for p, _, _ in hist)
        if not any(t == "POSIBLE BUG" for t, _ in al) and precio <= minimo * (1 - r["minimo_historico"]):
            al.append(("MINIMO HISTORICO", f"{round((1 - precio / minimo) * 100)}% debajo del minimo de {round(dias)} dias ({pesos(minimo)})"))
        if (lista and 1 - precio / lista >= r["inflado_descuento"]
                and lista > maximo * (1 + r["inflado_margen"]) and precio >= med * 0.95):
            al.append(("DESCUENTO INFLADO",
                       f"dice {round((1 - precio / lista) * 100)}% off sobre {pesos(lista)}, pero nunca costo mas de {pesos(maximo)} "
                       f"y su precio habitual es {pesos(med)}"))
    return al


# -------------------------------------------------------------------- avisos

def enviar_telegram(texto):
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[sin Telegram configurado]\n" + re.sub(r"<[^>]+>", "", texto) + "\n")
        return False
    datos = urllib.parse.urlencode({"chat_id": chat, "text": texto[:4000], "parse_mode": "HTML",
                                    "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", datos, timeout=20).read()
        return True
    except Exception as e:  # noqa: BLE001
        print("No se pudo enviar a Telegram:", e)
        return False


def armar_mensaje(p, alertas):
    e = html.escape
    lineas = [f"<b>{e(alertas[0][0])}</b> - {e(p['tienda'])}", e(p["titulo"]),
              f"<b>{pesos(p['precio'])}</b>" + (f" (lista {pesos(p['lista'])})" if p["lista"] else "")]
    lineas += [f"- {e(t)}: {e(d)}" for t, d in alertas]
    if p["vendedor"] and p["vendedor"].lower() != p["tienda"].lower():
        lineas.append(f"Vende: {e(p['vendedor'])}")
    if p["url"]:
        lineas.append(e(p["url"]))
    return "\n".join(lineas)


# ------------------------------------------------------------------ comandos

def correr(cfg, ruta_db, ahora=None, lector=leer):
    ahora = ahora or datetime.now(timezone.utc)
    reglas = dict(REGLAS)
    reglas.update(cfg.get("reglas") or {})
    excluir = cfg.get("excluir_titulos") or []
    c = abrir(ruta_db)
    cambiados, vistos, resumen = [], set(), []
    for tienda in cfg["tiendas"]:
        if not tienda.get("activa", True):
            continue
        total, errores = 0, 0
        for b in cfg["busquedas"]:
            try:
                prods, _ = lector(tienda, b["termino"], cfg.get("max_paginas", 4))
            except Exception as e:  # noqa: BLE001
                errores += 1
                print(f"  {tienda['nombre']} / {b['termino']}: ERROR {e}")
                continue
            for p in prods:
                if not p["precio"] or not p["titulo"] or (p["tienda"], p["sku"]) in vistos:
                    continue
                vistos.add((p["tienda"], p["sku"]))
                total += 1
                hist = guardar(c, p, b.get("categoria", ""), ahora, reglas["dias_historial"])
                if hist is not None:
                    cambiados.append((p, hist, b))
            time.sleep(PAUSA if lector is leer else 0)
        c.commit()
        resumen.append(f"{tienda['nombre']}: {total} productos" + (f", {errores} errores" if errores else ""))
        print(resumen[-1])

    pendientes = []
    for p, hist, b in cambiados:
        accesorio = es_accesorio(p["titulo"], excluir)
        titulo = p["titulo"].lower()
        aplica_piso = (not accesorio and b.get("piso")
                       and any(w.lower() in titulo for w in b.get("debe_contener", [b["termino"]])))
        clave = clave_modelo(p["titulo"], p["marca"], p["ean"])
        otras = [] if accesorio else precios_otras_tiendas(c, clave, p["tienda"], ahora)
        al = evaluar(p["precio"], p["lista"], hist, otras, reglas, b["piso"] if aplica_piso else None)
        nuevas = []
        for tipo, det in al:
            try:
                c.execute("INSERT INTO alertas VALUES(?,?,?,?,?)",
                          (p["tienda"], p["sku"], tipo, p["precio"], ahora.isoformat()))
                nuevas.append((tipo, det))
            except sqlite3.IntegrityError:
                pass  # ya se aviso por este precio
        if nuevas:
            pendientes.append((p, nuevas))
    c.commit()

    orden = ["POSIBLE BUG", "PRECIO ABSURDO", "DESCUENTO EXTREMO", "MAS BARATO QUE OTRAS TIENDAS",
             "MINIMO HISTORICO", "DESCUENTO INFLADO"]
    pendientes.sort(key=lambda x: orden.index(x[1][0][0]))
    tope = reglas["max_alertas_por_corrida"]
    for p, al in pendientes[:tope]:
        enviar_telegram(armar_mensaje(p, al))
        time.sleep(0.4 if os.environ.get("TELEGRAM_TOKEN") else 0)
    if len(pendientes) > tope:
        enviar_telegram(f"Hay {len(pendientes) - tope} alertas mas en esta corrida que no se enviaron (tope {tope}).")
    n_prod = c.execute("SELECT COUNT(*) FROM productos").fetchone()[0]
    print(f"Catalogo: {n_prod} productos. Alertas nuevas: {len(pendientes)}.")
    c.close()
    return pendientes, resumen


_MARCAS_PLATAFORMA = [
    ("VTEX", ("vtexassets", "vteximg", "vtex.com", "__runtime__")),
    ("Tiendanube", ("tiendanube", "mitiendanube", "d26lpennugtm8s")),
    ("Shopify", ("cdn.shopify.com",)),
    ("Magento", ("mage/", "magento", "static/version")),
    ("WooCommerce", ("woocommerce", "wp-content")),
    ("Next.js", ("__next_data__", "/_next/")),
]


def detectar_plataforma(texto):
    t = texto.lower()
    return next((n for n, marcas in _MARCAS_PLATAFORMA if any(m in t for m in marcas)), "desconocida")


def diagnostico(cfg, salida_md):
    filas = ["| Tienda | Plataforma | VTEX | HTML | Ejemplo |", "|---|---|---|---|---|"]
    todas = [dict(t, _prueba="smart tv") for t in cfg["tiendas"]]
    todas += [dict(t, _prueba=t.get("prueba", "smart tv")) for t in cfg.get("candidatas", [])]
    for t in todas:
        plataforma, n_vtex, n_html, ejemplo = "?", "-", "-", ""
        try:
            plataforma = detectar_plataforma(descargar(t["url"], intentos=1))
        except Exception as e:  # noqa: BLE001
            plataforma = f"sin acceso ({type(e).__name__} {getattr(e, 'code', '')})".strip()
        try:
            pv = leer_vtex(t, t["_prueba"], 1)
            n_vtex = str(len(pv))
            if pv:
                ejemplo = f"{pv[0]['titulo'][:40]} {pesos(pv[0]['precio'])}"
        except Exception as e:  # noqa: BLE001
            n_vtex = f"no ({getattr(e, 'code', type(e).__name__)})"
        if t.get("busqueda"):
            try:
                ph = leer_html(t, t["_prueba"], 1)
                n_html = str(len(ph))
                if ph and not ejemplo:
                    ejemplo = f"{ph[0]['titulo'][:40]} {pesos(ph[0]['precio'])}"
            except Exception as e:  # noqa: BLE001
                n_html = f"no ({getattr(e, 'code', type(e).__name__)})"
        filas.append(f"| {t['nombre']} | {plataforma} | {n_vtex} | {n_html} | {ejemplo} |")
        print(filas[-1])
        time.sleep(PAUSA)
    texto = "## Diagnostico de tiendas\n\n" + "\n".join(filas) + "\n"
    for ruta in filter(None, [salida_md, os.environ.get("GITHUB_STEP_SUMMARY")]):
        os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
        with open(ruta, "a", encoding="utf-8") as f:
            f.write(texto)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("comando", nargs="?", default="correr", choices=["correr", "diagnostico"])
    ap.add_argument("--config", default="tiendas.json")
    ap.add_argument("--db", default="data/precios.db")
    a = ap.parse_args()
    with open(a.config, encoding="utf-8") as f:
        cfg = json.load(f)
    if a.comando == "diagnostico":
        diagnostico(cfg, "data/diagnostico.md")
    else:
        correr(cfg, a.db)


if __name__ == "__main__":
    sys.exit(main())
