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
        self.assertEqual(self.tipos(200000, None, [(500000, None, 0.5)], []), [])

    def test_descuento_inflado(self):
        self.assertEqual(self.tipos(500000, 800000, self.H, []), ["DESCUENTO INFLADO"])

    def test_descuento_real_no_es_inflado(self):
        h = [(800000, None, 20), (500000, 800000, 10)]
        self.assertEqual(self.tipos(499000, 800000, h, []), [])

    def test_descuento_extremo_sin_historia(self):
        self.assertEqual(self.tipos(100000, 500000, [], []), ["DESCUENTO EXTREMO"])

    def test_otras_tiendas(self):
        self.assertEqual(self.tipos(300000, None, [], [500000, 520000]), ["MAS BARATO QUE OTRAS TIENDAS"])
        self.assertEqual(self.tipos(300000, None, [], [500000]), [])

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
