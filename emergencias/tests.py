from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import protocolo, servicios
from .models import Acuse, Incidente, Nodo, ReporteSafety, TramaRecibida, TramaSaliente

# Trama producida por CodecTramas (Dart) en Aplicacion/tool/simulador_radio.dart.
TRAMA_DART = bytes.fromhex(
    '4C755ABD4108709B04110D929430C2D1EBD06AD0A5FF48756D6F20656E20656469666963696F'
    '20430000000000000000000000000000459A'
)
PANEL = 0x0000CA0A


def alerta(msg_id=1, sender=0xAA, tipo=0x12, texto='Herido', lat=22.771, lon=-102.643, ttl=3):
    return protocolo.codificar(protocolo.TramaAlerta(msg_id, sender, tipo, 1_792_000_000, lat, lon, texto, ttl))


def acuse(msg_id, sender, tipo, ref):
    return protocolo.codificar(protocolo.TramaAcuse(msg_id, sender, tipo, ref, 1_792_000_100))


def safety(msg_id, sender):
    return protocolo.codificar(protocolo.TramaSafetyCheck(msg_id, sender, protocolo.A_SALVO, 1_792_000_200))


class ProtocoloTest(TestCase):
    def test_decodifica_trama_generada_por_dart(self):
        t = protocolo.decodificar(TRAMA_DART)
        self.assertEqual(t, protocolo.TramaAlerta(
            msg_id=1282759357, sender_id=1091072155, tipo=0x11, unix_time=1792058879,
            lat=22.771, lon=-102.643, texto='Humo en edificio C', ttl=4))

    def test_codifica_igual_que_dart(self):
        self.assertEqual(protocolo.codificar(protocolo.decodificar(TRAMA_DART)), TRAMA_DART)

    def test_ttl_no_afecta_el_crc(self):
        relevada = bytearray(TRAMA_DART)
        relevada[8] = 1  # un nodo intermedio decrementó el TTL
        self.assertEqual(protocolo.decodificar(bytes(relevada)).ttl, 1)

    def test_crc_invalido_se_descarta(self):
        corrupta = bytearray(TRAMA_DART)
        corrupta[30] ^= 0xFF
        self.assertIsNone(protocolo.decodificar(bytes(corrupta)))

    def test_ida_y_vuelta_acuse_y_safety(self):
        a = protocolo.TramaAcuse(7, 8, protocolo.ACK_LLEGUE, 9, 1000, 2)
        s = protocolo.TramaSafetyCheck(10, 11, protocolo.A_SALVO, 2000)
        self.assertEqual(len(protocolo.codificar(a)), protocolo.LONGITUD_ACUSE)
        self.assertEqual(len(protocolo.codificar(s)), protocolo.LONGITUD_SAFETY)
        self.assertEqual(protocolo.decodificar(protocolo.codificar(a)), a)
        self.assertEqual(protocolo.decodificar(protocolo.codificar(s)), s)

    def test_sos_sin_gps(self):
        t = protocolo.TramaAlerta(1, 2, protocolo.TIPO_SOS, 3, texto='ignorado')
        d = protocolo.decodificar(protocolo.codificar(t))
        self.assertIsNone(d.lat)
        self.assertEqual(d.texto, '')

    def test_texto_demasiado_largo(self):
        with self.assertRaises(ValueError):
            protocolo.codificar(protocolo.TramaAlerta(1, 2, 0x11, 3, texto='á' * 17))


@override_settings(PANEL_SENDER_ID=PANEL)
class ProcesadorTest(TestCase):
    def test_alerta_se_guarda_y_deduplica(self):
        servicios.procesar_trama(alerta(), rssi=-90, snr=4.5)
        servicios.procesar_trama(alerta(), rssi=-80, snr=6)  # retransmisión de otro nodo
        i = Incidente.objects.get()
        self.assertEqual((i.texto, i.saltos, i.rssi, i.estado), ('Herido', 2, -90, 'emitido'))
        self.assertEqual(Nodo.objects.get().tramas_recibidas, 2)
        self.assertEqual(TramaRecibida.objects.count(), 2)

    def test_trama_invalida_queda_en_bitacora(self):
        self.assertIsNone(servicios.procesar_trama(b'\x00\x01'))
        self.assertFalse(TramaRecibida.objects.get().valida)
        self.assertFalse(Incidente.objects.exists())

    def test_ciclo_de_vida_por_acuses(self):
        servicios.procesar_trama(alerta(msg_id=100))
        servicios.procesar_trama(acuse(1, 0xB1, protocolo.ACK_EN_CAMINO, 100))
        servicios.procesar_trama(acuse(2, 0xB2, protocolo.ACK_EN_CAMINO, 100))
        servicios.procesar_trama(acuse(3, 0xB2, protocolo.ACK_LLEGUE, 100))
        i = Incidente.objects.get()
        self.assertEqual(i.estado, 'en_asistencia')
        r = servicios.resumen_atencion(i)
        self.assertEqual((r.en_camino, r.en_sitio), (1, 1))
        servicios.procesar_trama(acuse(4, 0xB2, protocolo.ACK_RESUELTO, 100))
        i.refresh_from_db()
        self.assertEqual(i.estado, 'resuelto')

    def test_acuse_que_llega_antes_que_la_alerta(self):
        servicios.procesar_trama(acuse(1, 0xB1, protocolo.ACK_EN_CAMINO, 200))
        self.assertIsNone(Acuse.objects.get().incidente)
        servicios.procesar_trama(alerta(msg_id=200))
        self.assertEqual(Incidente.objects.get().estado, 'en_asistencia')

    def test_ultima_posicion_del_nodo(self):
        servicios.procesar_trama(alerta(lat=22.7701, lon=-102.6401))
        n = Nodo.objects.get()
        self.assertEqual((n.ultima_lat, n.ultima_lon), (22.7701, -102.6401))


@override_settings(PANEL_SENDER_ID=PANEL)
class AccionesTest(TestCase):
    def setUp(self):
        servicios.procesar_trama(alerta(msg_id=300))
        self.incidente = Incidente.objects.get()

    def test_no_puede_resolver_sin_llegar(self):
        with self.assertRaises(servicios.AccionNoPermitida):
            servicios.emitir_acuse(self.incidente, protocolo.ACK_RESUELTO)

    def test_despachar_llegar_y_resolver(self):
        servicios.emitir_acuse(self.incidente, protocolo.ACK_EN_CAMINO)
        servicios.emitir_acuse(self.incidente, protocolo.ACK_LLEGUE)
        servicios.emitir_acuse(self.incidente, protocolo.ACK_RESUELTO)
        self.incidente.refresh_from_db()
        self.assertEqual(self.incidente.estado, 'resuelto')
        salientes = [protocolo.decodificar(bytes.fromhex(t.bytes_hex))
                     for t in TramaSaliente.objects.order_by('creada_en')]
        self.assertEqual([t.tipo for t in salientes], [2, 3, 4])
        self.assertTrue(all(t.sender_id == PANEL and t.ref_msg_id == 300 for t in salientes))
        with self.assertRaises(servicios.AccionNoPermitida):
            servicios.emitir_acuse(self.incidente, protocolo.ACK_EN_CAMINO)

    def test_aviso_del_panel(self):
        i = servicios.emitir_aviso(0x11, 'Evacuar edificio B', 22.771, -102.643)
        self.assertEqual(i.origen, 'propio')
        trama = protocolo.decodificar(bytes.fromhex(TramaSaliente.objects.get().bytes_hex))
        self.assertEqual((trama.texto, trama.sender_id, trama.ttl), ('Evacuar edificio B', PANEL, protocolo.TTL_MAX))
        # Si la red nos devuelve nuestro propio aviso, no se duplica.
        servicios.procesar_trama(bytes.fromhex(TramaSaliente.objects.get().bytes_hex))
        self.assertEqual(Incidente.objects.filter(origen='propio').count(), 1)


@override_settings(PANEL_SENDER_ID=PANEL)
class CensoTest(TestCase):
    def test_cuenta_cada_persona_una_vez(self):
        servicios.procesar_trama(alerta(sender=0xC1))
        servicios.procesar_trama(safety(1, 0xC2))
        servicios.procesar_trama(safety(2, 0xC2))  # reenvío (CU-06 A1)
        servicios.procesar_trama(safety(3, 0xC3))
        r = servicios.censo_vigente()
        self.assertEqual((r.a_salvo, r.nodos_conocidos, r.sin_reporte), ([0xC2, 0xC3], 3, 1))

    def test_nuevo_censo_ignora_reportes_anteriores(self):
        servicios.procesar_trama(safety(1, 0xC2), ahora=timezone.now() - timedelta(minutes=5))
        servicios.iniciar_censo()
        self.assertEqual(servicios.censo_vigente().a_salvo, [])
        servicios.procesar_trama(safety(2, 0xC2))
        self.assertEqual(servicios.censo_vigente().a_salvo, [0xC2])
        self.assertEqual(ReporteSafety.objects.count(), 2)


@override_settings(PANEL_SENDER_ID=PANEL)
class VistasTest(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user('resp', password='x'))
        servicios.procesar_trama(alerta(msg_id=400))

    def test_paginas_cargan(self):
        i = Incidente.objects.get()
        for nombre, args in [('tablero', []), ('tablero_fragmento', []), ('incidentes', []),
                             ('incidente_detalle', [i.pk]), ('emitir_aviso', []), ('censo', []),
                             ('nodos', []), ('bitacora', [])]:
            with self.subTest(nombre):
                self.assertEqual(self.client.get(reverse(nombre, args=args)).status_code, 200)

    def test_requiere_sesion(self):
        self.client.logout()
        self.assertRedirects(self.client.get(reverse('tablero')), '/entrar/?next=/')

    def test_ubicacion_del_detalle_con_punto_decimal(self):
        i = Incidente.objects.get()
        self.assertContains(self.client.get(reverse('incidente_detalle', args=[i.pk])), '22.771000, -102.643000')

    def test_coordenadas_del_mapa_con_punto_decimal(self):
        # Con es-mx Django escribiría "22,77481" y Leaflet recibiría NaN.
        for nombre in ['tablero', 'emitir_aviso']:
            with self.subTest(nombre):
                self.assertContains(self.client.get(reverse(nombre)), 'data-norte="22.77481"')

    def test_mapa_json(self):
        datos = self.client.get(reverse('mapa_datos')).json()
        self.assertEqual(len(datos['incidentes']), 1)
        self.assertEqual(datos['incidentes'][0]['prioridad'], 'amarillo')

    def test_emitir_aviso_desde_formulario(self):
        r = self.client.post(reverse('emitir_aviso'), {'tipo': 0x13, 'texto': 'Grieta en muro', 'lat': '', 'lon': ''})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(TramaSaliente.objects.count(), 1)

    def test_aviso_fuera_del_campus_se_rechaza(self):
        r = self.client.post(reverse('emitir_aviso'), {'tipo': 0x11, 'texto': 'x', 'lat': 19.4, 'lon': -99.1})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(TramaSaliente.objects.exists())

    def test_acuse_desde_detalle(self):
        i = Incidente.objects.get()
        self.client.post(reverse('incidente_acuse', args=[i.pk]), {'tipo': protocolo.ACK_EN_CAMINO})
        i.refresh_from_db()
        self.assertEqual(i.estado, 'en_asistencia')


class TeselasTest(TestCase):
    PNG = b'\x89PNG\r\n\x1a\nfalso'

    def setUp(self):
        import tempfile
        from unittest import mock

        from . import teselas
        self.teselas = teselas
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        ajuste = override_settings(TESELAS_DIR=self.dir.name)
        ajuste.enable()
        self.addCleanup(ajuste.disable)
        teselas._sin_red_hasta = 0.0
        self.urlopen = mock.patch('emergencias.teselas.urllib.request.urlopen').start()
        self.addCleanup(mock.patch.stopall)
        self.client.force_login(get_user_model().objects.create_user('resp', password='x'))
        x0, _, y0, _ = teselas.rango(17)
        self.url = reverse('tesela', args=[17, x0, y0])

    def responder(self, datos):
        self.urlopen.return_value.__enter__.return_value.read.return_value = datos

    def test_campus_completo_son_unas_300_teselas(self):
        total = sum((x1 - x0 + 1) * (y1 - y0 + 1)
                    for x0, x1, y0, y1 in (self.teselas.rango(z) for z in range(15, 20)))
        self.assertEqual(total, 298)

    def test_descarga_una_vez_y_luego_sirve_sin_red(self):
        self.responder(self.PNG)
        r = self.client.get(self.url)
        self.assertEqual((r.status_code, r['Content-Type'], r.content), (200, 'image/png', self.PNG))
        self.urlopen.side_effect = OSError('sin red')
        self.assertEqual(self.client.get(self.url).content, self.PNG)
        self.assertEqual(self.urlopen.call_count, 1)

    def test_sin_red_ni_cache_da_404_y_no_reintenta_enseguida(self):
        self.urlopen.side_effect = OSError('sin red')
        self.assertEqual(self.client.get(self.url).status_code, 404)
        x0, _, y0, _ = self.teselas.rango(18)
        self.assertEqual(self.client.get(reverse('tesela', args=[18, x0, y0])).status_code, 404)
        self.assertEqual(self.urlopen.call_count, 1)

    def test_fuera_del_campus_no_se_descarga(self):
        for z, x, y in [(17, 0, 0), (12, 800, 1800), (20, 1, 1)]:
            self.assertEqual(self.client.get(reverse('tesela', args=[z, x, y])).status_code, 404)
        self.urlopen.assert_not_called()

    def test_requiere_sesion(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
