from django.urls import path

from . import views

urlpatterns = [
    path('', views.tablero, name='tablero'),
    path('tablero/lateral/', views.tablero_fragmento, name='tablero_fragmento'),
    path('api/mapa/', views.mapa_datos, name='mapa_datos'),
    path('teselas/<int:z>/<int:x>/<int:y>.png', views.tesela, name='tesela'),
    path('incidentes/', views.incidentes, name='incidentes'),
    path('incidentes/<int:pk>/', views.incidente_detalle, name='incidente_detalle'),
    path('incidentes/<int:pk>/acuse/', views.incidente_acuse, name='incidente_acuse'),
    path('aviso/', views.emitir_aviso, name='emitir_aviso'),
    path('censo/', views.censo, name='censo'),
    path('reporte/', views.reporte, name='reporte'),
    path('reporte/incidentes.csv', views.reporte_csv, name='reporte_csv'),
]
