import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import rastreador as r

T = {"nombre": "Tienda", "url": "https://tienda.test", "prefijo_producto": "/p/"}


class Numeros(unittest.TestCase):
    def test_formatos(self):
        self.assertEqual(r.numero("$1.749.999"), 1749999)
        self.assertEqual(r.numero("1.749.999,50"), 1749999.5)
        self.assertEqual(r.numero("1749999.00"), 1749999)
        self.assertEqual(r.numero(349999), 349999)
        self.assertEqual(r.numero([{"amounts": [{"min": 899999, "max": 899999}]}]), 899999)
        self.assertIsNone(r.numero("consultar"))
        self.assertIsNone(r.numero(0))


class Claves(unittest.TestCase):
    def test_mismo_modelo_distinta_tienda(self):
        a = r.clave_modelo('Smart TV Samsung QLED 4K UHD 55" QN55Q6FAAG', "Samsung")
        b = r.clave_modelo("Televisor Samsung 55 pulgadas QN55Q6FAAG QLED", "SAMSUNG")
        self.assertEqual(a, b)
        self.assertEqual(a, "SAMSUNG:QN55Q6FAAG")

    def test_ignora_unidades(self):
        self.assertEqual(r.clave_modelo("Celular Samsung 128GB 5000mAh", "Samsung"), "")
        self.assertEqual(r.clave_modelo('Celular Samsung Galaxy A56 5g 8/256GB 6.6"', "Samsung"), "")
        self.assertEqual(r.clave_modelo("Notebook HP 8/512gb 15-FD1340LA", "HP"), "HP:15FD1340LA")

    def test_ean(self):
        self.assertEqual(r.clave_modelo("x", "y", "7790001234567"), "EAN:7790001234567")

    def test_accesorio(self):
        self.assertTrue(r.es_accesorio("Soporte para TV 55", ["soporte"]))
        self.assertFalse(r.es_accesorio("Smart TV 55", ["soporte"]))


class Lectores(unittest.TestCase):
    def test_vtex(self):
        datos = [{"productId": "1", "productName": "TV Noblex 32 DB32X301", "brand": "Noblex",
                  "link": "https://tienda.test/tv/p",
                  "items": [{"itemId": "11", "ean": "7790001234567", "sellers": [
                      {"sellerName": "A", "commertialOffer": {"Price": 400000, "ListPrice": 500000, "AvailableQuantity": 3}},
                      {"sellerName": "B", "commertialOffer": {"Price": 350000, "ListPrice": 350000, "AvailableQuantity": 1}},
                      {"sellerName": "C", "commertialOffer": {"Price": 100, "ListPrice": 100, "AvailableQuantity": 0}}]}]}]
        p = r.interpretar_vtex(T, datos)
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0]["precio"], 350000)
        self.assertIsNone(p[0]["lista"])
        self.assertEqual(p[0]["vendedor"], "B")

    def test_html_json_ld_y_next(self):
        ld = {"@type": "Product", "name": "Heladera Gafa HGF388", "sku": "777",
              "brand": {"name": "Gafa"}, "offers": {"price": "899999.00", "url": "/heladera-777"}}
        nxt = {"props": {"pageProps": {"items": [
            {"id": "503075", "title": "TV Noblex 32 DB32X301", "slug": "tv-noblex-503075",
             "salePrices": [{"amounts": [{"min": 349999}]}], "listPrices": [{"amounts": [{"min": 489999}]}],
             "brand": {"name": "Noblex"}},
            {"id": "envio", "name": "Envio a domicilio", "price": 5000}]}}}
        pagina = (f'<script type="application/ld+json">{json.dumps(ld)}</script>'
                  f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(nxt)}</script>'
                  "<script>var x = {price: 1}</script>")
        p = {x["sku"]: x for x in r.extraer_productos_html(pagina, T)}
        self.assertEqual(set(p), {"777", "503075"})
        self.assertEqual(p["777"]["precio"], 899999)
        self.assertEqual(p["503075"]["lista"], 489999)
        self.assertEqual(p["503075"]["url"], "https://tienda.test/p/tv-noblex-503075/")

    def test_shopify(self):
        datos = {"products": [{"title": "Smart Tv Kanji KJ-32MT005", "handle": "tv-kanji", "vendor": "Kanji",
                               "product_type": "Televisores", "variants": [
                                   {"id": 1, "title": "Default Title", "price": "289999.00",
                                    "compare_at_price": "369999.00", "available": True},
                                   {"id": 2, "title": "Negro", "price": "1.00", "available": False}]}]}
        p = r.interpretar_shopify(T, datos)
        self.assertEqual(len(p), 1)
        self.assertEqual((p[0]["precio"], p[0]["lista"], p[0]["cat_real"]), (289999, 369999, "Televisores"))
        self.assertEqual(p[0]["url"], "https://tienda.test/products/tv-kanji")

    def test_magento(self):
        pagina = """<li class="item product product-item"><div class="product-item-info" data-container="product-grid">
          <strong><a class="product-item-link" href="https://tienda.test/aire-hyundai.html"> Aire Acondicionado Hyundai HY11NV-3200FCW </a></strong>
          <div class="price-box" data-product-id="39697"><span class="special-price">
          <span id="product-price-39697" data-price-amount="750000" data-price-type="finalPrice" class="price-wrapper "><span class="price">$750,000.00</span></span></span>
          <span class="old-price"><span id="old-price-39697" data-price-amount="900000" data-price-type="oldPrice" class="price-wrapper "></span></span></div></div></li>
          <li class="item product product-item"><div class="product-item-info"><a class="product-item-link" href="/x.html">Sin precio</a></div></li>"""
        p = r.interpretar_magento(T, pagina)
        self.assertEqual(len(p), 1)
        self.assertEqual((p[0]["sku"], p[0]["precio"], p[0]["lista"]), ("39697", 750000, 900000))
        self.assertEqual(p[0]["titulo"], "Aire Acondicionado Hyundai HY11NV-3200FCW")

    def test_compragamer(self):
        prods = [{"id_producto": 1674, "nombre": "Estabilizador TCA-1200N LYONN", "id_subcategoria": 31, "id_marca": 63,
                  "precioEspecial": 42890, "precioLista": 47656, "precioListaAnterior": None, "vendible": 1, "stock": 10},
                 {"id_producto": 2, "nombre": "Sin stock", "precioLista": 10, "vendible": 1, "stock": 0}]
        p = r.interpretar_compragamer(T, prods, {63: "Lyonn"}, {31: "Estabilizadores"})
        self.assertEqual(len(p), 1)
        self.assertEqual((p[0]["precio"], p[0]["marca"], p[0]["cat_real"]), (47656, "Lyonn", "Estabilizadores"))
        self.assertEqual(p[0]["url"], "https://compragamer.com/producto/Estabilizador_TCA_1200N_LYONN_1674")

    def test_gadnic(self):
        import html as h
        d = {"id": 46666, "post_title": "Aire Portátil Gadnic 2300 Frigorías", "precio": 579999, "precio_tachado": 724999,
             "marca": "Gadnic", "stock": 5, "en_pausa": 0, "categoria_principal_name": "Aires Acondicionados",
             "modelos": [{"ean": "7799219155399"}]}
        pagina = ('<a  href="/aires-acondicionados/aire-portatil"\n   class="bloque-blanco" title=\'x\' data-item-id="A" '
                  "data-product='" + h.escape(json.dumps(d)) + "'>")
        p = r.interpretar_gadnic({"nombre": "Gadnic", "url": "https://www.gadnic.com.ar"}, pagina)
        self.assertEqual(len(p), 1)
        self.assertEqual((p[0]["precio"], p[0]["lista"], p[0]["ean"]), (579999, 724999, "7799219155399"))
        self.assertEqual(p[0]["url"], "https://www.gadnic.com.ar/aires-acondicionados/aire-portatil")

    def test_barrido_y_estreno(self):
        cfg = {"tiendas": [{"nombre": "A", "url": "https://a.test"}, {"nombre": "B", "url": "https://b.test"},
                           {"nombre": "C", "url": "https://c.test"}],
               "busquedas": [{"termino": "notebook"}]}
        activas = {"A", "B"}
        precios = {"A": 1749999, "B": 1549999, "C": 1199000}

        def lector(tienda, termino, mp):
            return [r._producto(tienda, "1", "Notebook HP 15-FC0235LA", precios[tienda["nombre"]], None,
                                "", "HP")], "prueba"

        cfg2 = dict(cfg, tiendas=[t for t in cfg["tiendas"] if t["nombre"] in activas])
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "p.db")
            t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
            al, _ = r.correr(cfg2, db, t0, lector)
            self.assertEqual(al, [])
            # C entra por primera vez ya barata: avisa por cruce aunque sea primera carga
            al, _ = r.correr(cfg, db, t0 + timedelta(hours=1), lector)
            self.assertEqual([(p["tienda"], a[0][0]) for p, a in al], [("C", "MAS BARATO QUE OTRAS TIENDAS")])
            # no repite en la corrida siguiente ni en el barrido del dia siguiente (mismo precio)
            al, _ = r.correr(cfg, db, t0 + timedelta(hours=2), lector)
            self.assertEqual(al, [])
            al, _ = r.correr(cfg, db, t0 + timedelta(days=1, hours=1), lector)
            self.assertEqual(al, [])
            # B baja sin cambiar C... y A ya estaba barata desde antes: el barrido la encuentra
            precios["C"] = 1700000
            precios["A"] = 1100000
            al, _ = r.correr(cfg, db, t0 + timedelta(days=1, hours=2), lector)
            self.assertEqual([p["tienda"] for p, _ in al], ["A"])

    def test_excedente_se_envia_despues(self):
        cfg = {"tiendas": [{"nombre": "A", "url": "https://a.test"}], "busquedas": [{"termino": "x"}],
               "reglas": {"max_alertas_por_corrida": 2}}
        precios = {i: 100000 for i in range(5)}

        def lector(tienda, termino, mp):
            return [r._producto(tienda, str(i), f"Producto MOD{i}X{i}", precios[i], None) for i in precios], "p"

        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "p.db")
            t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
            for dia in range(10):
                r.correr(cfg, db, t0 + timedelta(days=dia), lector)
            for i in precios:
                precios[i] = 30000
            enviados = []
            for h in range(1, 5):
                al, _ = r.correr(cfg, db, t0 + timedelta(days=10, hours=h), lector)
                enviados += [p["sku"] for p, _ in al[:2]]
            self.assertEqual(sorted(enviados), ["0", "1", "2", "3", "4"])

    def test_empareja_por_modelo_y_marca(self):
        c = r.abrir(":memory:")
        ahora = datetime(2026, 11, 1, tzinfo=timezone.utc)
        for tienda, sku, titulo, marca, precio, ean in [
                ("Naldo", "1", "Heladera Hisense 324l Rt320n3asa Inoxidable", "Hisense", 999999, "7791"),
                ("OnCity", "2", "Heladera Hisense Rt320n3asa Inox", "HISENSE", 949999, ""),
                ("Otra", "3", "Heladera Gafa Rt320n3asa", "Gafa", 100, ""),
                ("Newsan", "4", "Heladera Hisense Top Mount 320L", "", 625999, "")]:
            r.guardar(c, r._producto({"nombre": tienda, "url": "https://x"}, sku, titulo, precio, None, "", marca, ean),
                      "", ahora, 60)
        self.assertEqual(r.precios_otras_tiendas(c, "", "Newsan", ahora, sku="4"), [])
        c.execute("UPDATE productos SET modelo='RT320N3ASA', marca='Hisense' WHERE tienda='Newsan'")
        otras = sorted(r.precios_otras_tiendas(c, "", "Newsan", ahora, sku="4"))
        self.assertEqual(otras, [("Naldo", 999999), ("OnCity", 949999)])

    def test_modelo_desde_pagina(self):
        pagina = '<td class="col data" data-th="Modelo">94RT320N3ASA</td><td data-th="Marca">HISENSE</td>'
        m = r._RE_MODELO_PAGINA.search(pagina).group(1)
        self.assertEqual(r.re.sub("^94", "", m), "RT320N3ASA")
        self.assertEqual(r._RE_MARCA_PAGINA.search(pagina).group(1), "HISENSE")

    def test_aviso_tienda_caida(self):
        cfg = {"tiendas": [{"nombre": "A", "url": "https://a.test"}], "busquedas": [{"termino": "x"}]}
        estado = {"falla": False}
        enviados = []
        original = r.enviar_telegram
        r.enviar_telegram = lambda texto: enviados.append(texto)

        def lector(tienda, termino, mp):
            if estado["falla"]:
                raise OSError("403")
            return [r._producto(tienda, "1", "Producto ABC123XY", 1000)], "p"
        try:
            with tempfile.TemporaryDirectory() as d:
                db = os.path.join(d, "p.db")
                t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
                r.correr(cfg, db, t0, lector)
                estado["falla"] = True
                r.correr(cfg, db, t0 + timedelta(hours=1), lector)
                self.assertEqual(enviados, [])
                r.correr(cfg, db, t0 + timedelta(hours=2), lector)
                r.correr(cfg, db, t0 + timedelta(hours=3), lector)
                self.assertEqual(len(enviados), 1)
                estado["falla"] = False
                r.correr(cfg, db, t0 + timedelta(hours=4), lector)
                self.assertIn("volvio", enviados[-1])
        finally:
            r.enviar_telegram = original

    def test_cada_horas(self):
        cfg = {"tiendas": [{"nombre": "A", "url": "https://a.test", "cada_horas": 3}],
               "busquedas": [{"termino": "x"}]}
        llamadas = []

        def lector(tienda, termino, mp):
            llamadas.append(1)
            return [r._producto(tienda, "1", "Producto ABC123XY", 1000)], "prueba"

        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "p.db")
            t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
            for h in (0, 0.5, 1, 3, 3.5):
                r.correr(cfg, db, t0 + timedelta(hours=h), lector)
            self.assertEqual(len(llamadas), 2)

    def test_plataforma(self):
        self.assertEqual(r.detectar_plataforma('<img src="https://x.vtexassets.com/a.png">'), "VTEX")
        self.assertEqual(r.detectar_plataforma("<html></html>"), "desconocida")


class Deteccion(unittest.TestCase):
    H = [(500000, None, 30)]

    def tipos(self, *a, **k):
        return [t for t, _ in r.evaluar(*a, **k)]

    def test_bug(self):
        self.assertEqual(self.tipos(200000, None, self.H, []), ["POSIBLE BUG"])

    def test_baja_normal_no_avisa(self):
        self.assertEqual(self.tipos(470000, None, self.H, []), [])

    def test_minimo_historico(self):
        self.assertEqual(self.tipos(430000, None, self.H, []), ["MINIMO HISTORICO"])

    def test_poca_historia_no_avisa(self):
        self.assertEqual(self.tipos(200000, None, [(500000, None, 0.2)], []), [])
        self.assertEqual(self.tipos(200000, None, [(500000, None, 0.6)], []), ["POSIBLE BUG"])

    def test_descuento_inflado(self):
        self.assertEqual(self.tipos(500000, 800000, self.H, []), ["DESCUENTO INFLADO"])

    def test_descuento_real_no_es_inflado(self):
        h = [(800000, None, 20), (560000, 800000, 10)]
        self.assertEqual(self.tipos(559000, 800000, h, []), [])

    def test_descuento_extremo_sin_historia(self):
        self.assertEqual(self.tipos(100000, 500000, [], []), ["DESCUENTO EXTREMO"])

    def test_otras_tiendas(self):
        self.assertEqual(self.tipos(300000, None, [], [500000, 520000]), ["MAS BARATO QUE OTRAS TIENDAS"])
        self.assertEqual(self.tipos(300000, None, [], [500000]), [])
        # una tienda carisima no alcanza: tiene que ser bastante mas barato que la mas barata
        self.assertEqual(self.tipos(950000, None, [], [("A", 999999), ("B", 2399999), ("C", 2399999)]), [])
        al = r.evaluar(1249000, None, [], [("OnCity", 1749999), ("Naldo", 1549999), ("Cetrogar", 1899999)])
        self.assertEqual([x for x, _ in al], ["MAS BARATO QUE OTRAS TIENDAS"])
        self.assertIn("Naldo $1.549.999", al[0][1])

    def test_nuevo_fuera_de_rango(self):
        v = [300000 + i * 20000 for i in range(20)]
        al = r.evaluar(35000, None, [], [], vecinos=v, propio=True)
        self.assertEqual([t for t, _ in al], ["NUEVO FUERA DE RANGO"])
        self.assertIn("digito", al[0][1])
        self.assertEqual(self.tipos(250000, None, [], [], vecinos=v, propio=True), [])
        # vendedor externo: mas exigente
        self.assertEqual(self.tipos(100000, None, [], [], vecinos=v, propio=False), [])
        self.assertEqual(self.tipos(100000, None, [], [], vecinos=v, propio=True), ["NUEVO FUERA DE RANGO"])
        # con historial propio manda el historial
        self.assertEqual(self.tipos(35000, None, [(36000, None, 30)], [], vecinos=v), [])

    def test_piso(self):
        self.assertEqual(self.tipos(9999, None, [], [], piso=90000), ["PRECIO ABSURDO"])


class Corrida(unittest.TestCase):
    def test_de_punta_a_punta(self):
        cfg = {"tiendas": [{"nombre": "A", "url": "https://a.test"}, {"nombre": "B", "url": "https://b.test"},
                           {"nombre": "C", "url": "https://c.test"}],
               "busquedas": [{"termino": "smart tv", "categoria": "TV", "piso": 90000, "debe_contener": ["tv"]}],
               "excluir_titulos": ["soporte"]}
        precios = {"A": 500000, "B": 510000, "C": 505000}
        extra = []

        def lector(tienda, termino, mp):
            n = tienda["nombre"]
            ps = [r._producto(tienda, "1", "Smart TV Samsung 55 QN55Q6FAAG", precios[n], None,
                              tienda["url"] + "/tv", "Samsung")]
            if n == "A":
                ps += extra
            return ps, "prueba"

        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "p.db")
            t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
            al, _ = r.correr(cfg, db, t0, lector)
            self.assertEqual(al, [])
            for dia in range(1, 10):
                al, _ = r.correr(cfg, db, t0 + timedelta(days=dia), lector)
                self.assertEqual(al, [])
            # el catalogo crece y un soporte barato no dispara "precio absurdo"
            extra.append(r._producto(cfg["tiendas"][0], "2", "Soporte para TV 55", 20000, None, "", "Generico"))
            al, _ = r.correr(cfg, db, t0 + timedelta(days=10), lector)
            self.assertEqual(al, [])
            # error de precio en A
            precios["A"] = 50000
            al, _ = r.correr(cfg, db, t0 + timedelta(days=11), lector)
            self.assertEqual(len(al), 1)
            tipos = [t for t, _ in al[0][1]]
            self.assertIn("POSIBLE BUG", tipos)
            self.assertIn("PRECIO ABSURDO", tipos)
            self.assertIn("MAS BARATO QUE OTRAS TIENDAS", tipos)
            self.assertIn("50.000", r.armar_mensaje(*al[0]))
            self.assertIn("https://listado.mercadolibre.com.ar/Samsung-QN55Q6FAAG", r.armar_mensaje(*al[0]))
            # no repite el aviso si el precio sigue igual
            al, _ = r.correr(cfg, db, t0 + timedelta(days=11, hours=1), lector)
            self.assertEqual(al, [])
            c = r.abrir(db)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM productos").fetchone()[0], 4)


if __name__ == "__main__":
    unittest.main()
