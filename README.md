# Rastreador de ofertas

Recorre tiendas online cada 30 minutos, guarda el historial de precios y avisa por Telegram cuando encuentra:

- **POSIBLE BUG**: el precio cae 45% o más contra su precio habitual.
- **DESCUENTO EXTREMO**: 70% o más contra el precio de lista publicado.
- **MAS BARATO QUE OTRAS TIENDAS**: 30% menos que la mediana del mismo modelo en otras tiendas.
- **NUEVO FUERA DE RANGO**: producto sin historial, muy por debajo del rango de su categoría real en la tienda (avisa si con un dígito más sería un precio normal).
- **MINIMO HISTORICO**: 10% por debajo del mínimo anterior (oferta real).
- **DESCUENTO INFLADO**: anuncia un descuento sobre un "antes" que nunca cobró.

Cada alerta trae un link para comparar el producto en Mercado Libre.

## Puesta en marcha

1. **Telegram**: hablale a `@BotFather`, mandá `/newbot` y guardá el token. Escribile cualquier cosa a tu bot nuevo y después abrí `https://api.telegram.org/bot<TOKEN>/getUpdates` para ver tu `chat.id`.
2. **Secrets**: en el repo, Settings → Secrets and variables → Actions, creá `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID`.
3. **Diagnóstico**: pestaña Actions → "Rastreador de ofertas" → Run workflow → modo `diagnostico`. Al terminar, el resumen muestra una tabla con qué tiendas se pueden leer y con qué plataforma.
4. Después corre solo. El historial queda en la rama `datos` (`precios.db`).

## Ajustes

Todo está en `tiendas.json`:

- `busquedas`: qué se busca en cada tienda.
- `tiendas`: las que se rastrean. `candidatas`: las que solo se prueban en el diagnóstico.
- `reglas`: para cambiar umbrales, por ejemplo `{"caida_bug": 0.5}`. Los valores por defecto están en `REGLAS` dentro de `rastreador.py`.
- `divisor` (por tienda): usar `100` si esa tienda publica los precios en centavos.

## Notas

- Sin Telegram configurado, las alertas se imprimen en el registro de la corrida.
- En un repo privado, GitHub da 2.000 minutos gratis por mes: cada 30 minutos los supera. O se hace público, o se cambia el `cron` a una vez por hora.
- Pruebas: `python -m unittest test_rastreador`.
